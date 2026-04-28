import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp

from app.ingestion.twitch import DailySessionResolver
from app.models.chat import ChannelInfo, ChatMessage

logger = logging.getLogger(__name__)

TWITCH_IRC_WS = "wss://irc-ws.chat.twitch.tv:443"
IRC_PRIVMSG_RE = re.compile(r"^(?::(?P<prefix>\S+) )?PRIVMSG #(?P<channel>\S+) :(?P<message>.*)$")


def parse_irc_tags(raw_tags: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    if not raw_tags:
        return tags
    for item in raw_tags.split(";"):
        key, _, value = item.partition("=")
        tags[key] = _unescape_tag(value)
    return tags


def parse_badges(value: str) -> list[dict[str, str]]:
    if not value:
        return []
    badges: list[dict[str, str]] = []
    for badge in value.split(","):
        name, _, version = badge.partition("/")
        if name:
            badges.append({"set_id": name, "id": version})
    return badges


def parse_irc_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def parse_emotes(value: str, message_text: str) -> list[dict[str, Any]]:
    if not value:
        return []
    emotes: list[dict[str, Any]] = []
    for item in value.split("/"):
        emote_id, _, ranges = item.partition(":")
        for range_value in ranges.split(","):
            start_raw, _, end_raw = range_value.partition("-")
            if not start_raw or not end_raw:
                continue
            start = int(start_raw)
            end = int(end_raw)
            emotes.append(
                {
                    "type": "emote",
                    "text": message_text[start : end + 1],
                    "emote": {"id": emote_id},
                    "position": {"start": start, "end": end},
                }
            )
    return emotes


def parse_reply(tags: dict[str, str]) -> dict[str, str] | None:
    parent_id = tags.get("reply-parent-msg-id")
    if not parent_id:
        return None
    return {
        "parent_message_id": parent_id,
        "parent_user_id": tags.get("reply-parent-user-id", ""),
        "parent_user_login": tags.get("reply-parent-user-login", ""),
        "parent_display_name": tags.get("reply-parent-display-name", ""),
        "parent_message_body": tags.get("reply-parent-msg-body", ""),
        "thread_parent_message_id": tags.get("reply-thread-parent-msg-id", ""),
        "thread_parent_user_login": tags.get("reply-thread-parent-user-login", ""),
    }


class TwitchIrcClient:
    def __init__(
        self,
        channels: list[str],
        on_message: Callable[[ChatMessage], Awaitable[None]],
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        username: str = "",
        access_token: str = "",
    ) -> None:
        self._channels = [channel.lower().lstrip("#") for channel in channels]
        self._on_message = on_message
        self._on_status = on_status
        self._username = username.lower() if username else f"justinfan{random.randint(10000, 999999)}"
        self._access_token = access_token
        self._session_resolver = DailySessionResolver()
        self._stopped = asyncio.Event()

    async def run_forever(self) -> None:
        backoff_seconds = 2
        while not self._stopped.is_set():
            try:
                await self._connect_once()
                backoff_seconds = 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Twitch IRC connection failed: %s", exc)
                await self._on_status({"state": "irc_error", "detail": str(exc)})
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 60)

    async def stop(self) -> None:
        self._stopped.set()

    async def _connect_once(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(TWITCH_IRC_WS, heartbeat=30) as ws:
                await self._authenticate(ws)
                await self._join_channels(ws)
                await self._on_status({"state": "irc_connected", "channels": self._channels, "username": self._username})

                async for ws_message in ws:
                    if ws_message.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_raw_message(ws, ws_message.data)
                    elif ws_message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                        break

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        await ws.send_str("CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
        if self._access_token:
            token = self._access_token
            if not token.startswith("oauth:"):
                token = f"oauth:{token}"
            await ws.send_str(f"PASS {token}")
        else:
            await ws.send_str("PASS SCHMOOPIIE")
        await ws.send_str(f"NICK {self._username}")

    async def _join_channels(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        for channel in self._channels:
            await ws.send_str(f"JOIN #{channel}")
            await self._on_status(
                {
                    "state": "irc_joined",
                    "channel": ChannelInfo(
                        channel_id="",
                        channel_login=channel,
                        channel_display_name=channel,
                        status="irc_joined",
                    ).model_dump(),
                }
            )

    async def _handle_raw_message(self, ws: aiohttp.ClientWebSocketResponse, raw_data: str) -> None:
        for raw_line in raw_data.split("\r\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("PING "):
                await ws.send_str(line.replace("PING", "PONG", 1))
                continue
            message = self._normalize_privmsg(line)
            if message:
                await self._on_message(message)

    def _normalize_privmsg(self, line: str) -> ChatMessage | None:
        tags: dict[str, str] = {}
        body = line
        if line.startswith("@"):
            raw_tags, _, body = line.partition(" ")
            tags = parse_irc_tags(raw_tags[1:])

        match = IRC_PRIVMSG_RE.match(body)
        if not match:
            return None

        channel_login = match.group("channel").lower()
        message_text = match.group("message")
        event_ts = parse_irc_timestamp(tags.get("tmi-sent-ts"))
        channel_id = tags.get("room-id") or channel_login
        session_id, session_date = self._session_resolver.session_for(channel_id, event_ts)
        emotes = parse_emotes(tags.get("emotes", ""), message_text)

        return ChatMessage(
            message_id=tags.get("id") or f"irc:{channel_login}:{int(event_ts.timestamp() * 1000)}:{hash(line)}",
            eventsub_message_id="",
            channel_id=channel_id,
            channel_login=channel_login,
            channel_display_name=channel_login,
            session_id=session_id,
            session_date=session_date,
            chatter_user_id=tags.get("user-id", ""),
            chatter_login=_login_from_prefix(match.group("prefix")),
            chatter_display_name=tags.get("display-name") or _login_from_prefix(match.group("prefix")),
            message_text=message_text,
            message_fragments=[{"type": "text", "text": message_text}],
            badges=parse_badges(tags.get("badges", "")),
            emotes=emotes,
            mentions=[],
            reply=parse_reply(tags),
            raw_event={"source": "twitch_irc", "line": line, "tags": tags},
            event_ts=event_ts,
        )


def _login_from_prefix(prefix: str | None) -> str:
    if not prefix:
        return ""
    login, _, _rest = prefix.partition("!")
    return login.lower()


def _unescape_tag(value: str) -> str:
    return (
        value.replace(r"\s", " ")
        .replace(r"\:", ";")
        .replace(r"\\", "\\")
        .replace(r"\r", "\r")
        .replace(r"\n", "\n")
    )
