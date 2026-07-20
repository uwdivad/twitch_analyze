import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import aiohttp

from app.models.chat import ChannelInfo, ChatMessage

logger = logging.getLogger(__name__)

TWITCH_EVENTSUB_WS = "wss://eventsub.wss.twitch.tv/ws"
TWITCH_HELIX = "https://api.twitch.tv/helix"
TWITCH_VALIDATE = "https://id.twitch.tv/oauth2/validate"

# A connection that stayed up at least this long counts as "sustained" and resets backoff.
BACKOFF_RESET_SECONDS = 60.0


def parse_twitch_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


class DailySessionResolver:
    def session_for(self, channel_id: str, event_ts: datetime) -> tuple[str, date]:
        session_date = event_ts.date()
        return f"{channel_id}:{session_date.isoformat()}", session_date


class TwitchEventSubClient:
    def __init__(
        self,
        client_id: str,
        access_token: str,
        channels: list[str],
        user_id: str,
        on_message: Callable[[ChatMessage], Awaitable[None]],
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._client_id = client_id
        self._access_token = access_token
        self._channels = channels
        self._user_id = user_id
        self._on_message = on_message
        self._on_status = on_status
        self._session_resolver = DailySessionResolver()
        self._http: aiohttp.ClientSession | None = None
        self._channels_by_id: dict[str, ChannelInfo] = {}
        self._stopped = asyncio.Event()
        self._reconnect_url: str | None = None
        self._resuming_session = False

    async def run_forever(self) -> None:
        backoff_seconds = 2
        async with aiohttp.ClientSession(headers=self._headers()) as http:
            self._http = http
            while not self._stopped.is_set():
                loop = asyncio.get_running_loop()
                attempt_started_at = loop.time()
                try:
                    if not self._channels_by_id:
                        # Resolve inside the retried section so a transient Helix failure
                        # at boot does not permanently kill EventSub ingestion.
                        self._channels_by_id = await self._resolve_channels()
                    await self._connect_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Twitch EventSub connection failed: %s", exc)
                    await self._on_status({"state": "error", "detail": str(exc)})
                if self._stopped.is_set():
                    break
                if loop.time() - attempt_started_at >= BACKOFF_RESET_SECONDS:
                    backoff_seconds = 2
                if self._reconnect_url:
                    # Twitch requested a session reconnect; dial the new URL immediately.
                    continue
                # Back off on clean closes too, not just exceptions, so a
                # server-initiated close cannot turn into a zero-delay busy loop.
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 60)

    async def stop(self) -> None:
        self._stopped.set()

    async def _connect_once(self) -> None:
        # A pending reconnect_url is consumed exactly once; if this attempt fails,
        # the next one falls back to the default EventSub URL.
        url = self._reconnect_url or TWITCH_EVENTSUB_WS
        self._resuming_session = self._reconnect_url is not None
        self._reconnect_url = None
        await self._on_status(
            {
                "state": "connecting",
                "channels": [c.channel_login for c in self._channels_by_id.values()],
                "resuming": self._resuming_session,
            }
        )
        async with self._http.ws_connect(url, heartbeat=30) as ws:  # type: ignore[union-attr]
            async for ws_message in ws:
                if ws_message.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_payload(ws_message.json())
                    if self._reconnect_url:
                        # Twitch asked for a session reconnect: drop this connection so
                        # run_forever can dial the new URL before messages are re-routed.
                        await ws.close()
                        break
                elif ws_message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                    break

    async def _handle_ws_payload(self, payload: dict[str, Any]) -> None:
        metadata = payload.get("metadata", {})
        message_type = metadata.get("message_type")

        if message_type == "session_welcome":
            session_id = payload["payload"]["session"]["id"]
            await self._on_status({"state": "connected", "session_id": session_id})
            if self._resuming_session:
                # Connected via reconnect_url: Twitch carries subscriptions over to the
                # resumed session, so re-subscribing is unnecessary (and would duplicate).
                self._resuming_session = False
            else:
                await self._subscribe_channels(session_id)
            return

        if message_type == "session_reconnect":
            reconnect_url = payload["payload"]["session"].get("reconnect_url")
            if reconnect_url:
                self._reconnect_url = reconnect_url
            await self._on_status({"state": "reconnect_requested", "reconnect_url": reconnect_url})
            return

        if message_type == "notification":
            subscription_type = payload.get("payload", {}).get("subscription", {}).get("type")
            if subscription_type == "channel.chat.message":
                message = self._normalize_message(payload)
                await self._on_message(message)
            return

        if message_type == "revocation":
            await self._on_status({"state": "revoked", "payload": payload.get("payload", {})})
            return

        if message_type == "session_keepalive":
            return

        logger.debug("Unhandled EventSub message type: %s", message_type)

    async def _subscribe_channels(self, session_id: str) -> None:
        for channel in self._channels_by_id.values():
            body = {
                "type": "channel.chat.message",
                "version": "1",
                "condition": {
                    "broadcaster_user_id": channel.channel_id,
                    "user_id": self._user_id,
                },
                "transport": {
                    "method": "websocket",
                    "session_id": session_id,
                },
            }
            async with self._http.post(f"{TWITCH_HELIX}/eventsub/subscriptions", json=body) as response:  # type: ignore[union-attr]
                if response.status >= 400:
                    text = await response.text()
                    raise RuntimeError(f"failed to subscribe to {channel.channel_login}: {response.status} {text}")
                await self._on_status({"state": "subscribed", "channel": channel.model_dump()})

    async def _resolve_channels(self) -> dict[str, ChannelInfo]:
        if not self._channels:
            return {}

        params = [("login", channel) for channel in self._channels]
        async with self._http.get(f"{TWITCH_HELIX}/users", params=params) as response:  # type: ignore[union-attr]
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(f"failed to resolve Twitch channels: {response.status} {text}")
            payload = await response.json()

        channels: dict[str, ChannelInfo] = {}
        for item in payload.get("data", []):
            channel = ChannelInfo(
                channel_id=item["id"],
                channel_login=item["login"].lower(),
                channel_display_name=item.get("display_name") or item["login"],
                status="resolved",
            )
            channels[channel.channel_id] = channel
        return channels

    def _normalize_message(self, payload: dict[str, Any]) -> ChatMessage:
        metadata = payload.get("metadata", {})
        event = payload["payload"]["event"]
        channel = self._channels_by_id.get(
            event["broadcaster_user_id"],
            ChannelInfo(
                channel_id=event["broadcaster_user_id"],
                channel_login=event.get("broadcaster_user_login", ""),
                channel_display_name=event.get("broadcaster_user_name", ""),
            ),
        )

        event_ts = parse_twitch_timestamp(metadata.get("message_timestamp"))
        session_id, session_date = self._session_resolver.session_for(channel.channel_id, event_ts)
        message = event.get("message", {})
        fragments = message.get("fragments") or []

        return ChatMessage(
            message_id=event["message_id"],
            eventsub_message_id=metadata.get("message_id", ""),
            channel_id=channel.channel_id,
            channel_login=channel.channel_login,
            channel_display_name=channel.channel_display_name,
            session_id=session_id,
            session_date=session_date,
            chatter_user_id=event.get("chatter_user_id", ""),
            chatter_login=event.get("chatter_user_login", ""),
            chatter_display_name=event.get("chatter_user_name", ""),
            message_text=message.get("text", ""),
            message_fragments=fragments,
            badges=event.get("badges") or [],
            emotes=[fragment for fragment in fragments if fragment.get("type") == "emote"],
            mentions=[fragment for fragment in fragments if fragment.get("type") == "mention"],
            reply=event.get("reply"),
            raw_event=payload,
            event_ts=event_ts,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self._client_id,
            "Authorization": f"Bearer {self._access_token}",
        }


async def resolve_twitch_user_id(client_id: str, access_token: str) -> str:
    headers = {
        "Client-Id": client_id,
        "Authorization": f"OAuth {access_token}",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(TWITCH_VALIDATE) as response:
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(f"failed to validate Twitch token: {response.status} {text}")
            payload = await response.json()
            return payload["user_id"]
