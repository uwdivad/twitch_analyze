"""Find the VOD a platform is currently recording for a live channel.

Twitch keeps writing the "past broadcast" archive while a channel is live, so the
tail of that VOD is the last few minutes of the stream. There is no official
endpoint for "the VOD of the current stream"; this module asks Twitch's web GQL
API (same client as chat replay) for the channel's stream and its newest archive
video, then matches the two by start time.

Other platforms get their own ``LiveVodSource`` implementation; ``ClipService``
only depends on the protocol.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

from app.ingestion.twitch import parse_twitch_timestamp
from app.models.clips import LiveVod

__all__ = [
    "CHANNEL_LIVE_VOD_QUERY",
    "ChannelNotFoundError",
    "ChannelNotLiveError",
    "LiveVodSource",
    "NoLiveVodError",
    "TwitchLiveVodSource",
    "live_vod_from_gql",
    "parse_channel_reference",
]

CHANNEL_LIVE_VOD_QUERY = (
    "query($login: String!) { user(login: $login) { id login displayName "
    "stream { id createdAt } "
    "videos(first: 3, type: ARCHIVE, sort: TIME) { edges { node { id title lengthSeconds createdAt } } } } }"
)
# A VOD created this long before the stream counts as the stream's archive (the two
# timestamps are written by different Twitch systems and can differ by a few seconds).
_ARCHIVE_MATCH_SLACK = timedelta(minutes=2)

_LOGIN_RE = re.compile(r"^[a-z0-9_]{1,25}$")


class ChannelNotFoundError(Exception):
    """The platform has no channel with that login."""


class ChannelNotLiveError(Exception):
    """The channel exists but is not streaming right now."""


class NoLiveVodError(Exception):
    """The channel is live but no archive VOD is being recorded for it."""


class LiveVodSource(Protocol):
    platform: str

    async def resolve(self, channel_login: str) -> LiveVod: ...


def parse_channel_reference(value: str) -> str:
    """Return a lowercase channel login from a bare login or a Twitch channel URL."""
    candidate = (value or "").strip().lstrip("@").lower()
    if not candidate:
        raise ValueError("channel is empty")
    if "/" in candidate or "." in candidate:
        to_parse = candidate if "://" in candidate else f"https://{candidate}"
        parsed = urlparse(to_parse)
        host = (parsed.hostname or "").lower()
        if host != "twitch.tv" and not host.endswith(".twitch.tv"):
            raise ValueError(f"not a Twitch channel reference: {value!r}")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1:
            raise ValueError(f"no channel login in Twitch URL: {value!r}")
        candidate = parts[0]
    if not _LOGIN_RE.match(candidate):
        raise ValueError(f"invalid channel login: {value!r}")
    return candidate


def live_vod_from_gql(payload: dict[str, Any], channel_login: str) -> LiveVod:
    """Pick the in-progress archive VOD out of the channel query response."""
    data = (payload or {}).get("data") or {}
    user = data.get("user")
    if not user:
        raise ChannelNotFoundError(f"Twitch channel '{channel_login}' was not found")
    stream = user.get("stream")
    if not stream:
        raise ChannelNotLiveError(f"{user.get('displayName') or channel_login} is not live right now")
    stream_started = parse_twitch_timestamp(stream.get("createdAt"))

    edges = ((user.get("videos") or {}).get("edges")) or []
    for edge in edges:
        node = (edge or {}).get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict) or not node.get("id"):
            continue
        created_at = parse_twitch_timestamp(node.get("createdAt"))
        if created_at >= stream_started - _ARCHIVE_MATCH_SLACK:
            return LiveVod(
                platform="twitch",
                video_id=str(node["id"]),
                title=node.get("title") or "",
                channel_id=str(user.get("id") or ""),
                channel_login=user.get("login") or channel_login,
                channel_display_name=user.get("displayName") or user.get("login") or channel_login,
                created_at=created_at,
                duration_seconds=int(node.get("lengthSeconds") or 0),
            )
    raise NoLiveVodError(
        f"{user.get('displayName') or channel_login} is live but has no VOD of this stream yet "
        "(the channel may have past broadcasts disabled)"
    )


GqlFactory = Callable[[], AbstractAsyncContextManager[Any]]


class TwitchLiveVodSource:
    platform = "twitch"

    def __init__(self, gql_factory: GqlFactory) -> None:
        self._gql_factory = gql_factory

    async def resolve(self, channel_login: str) -> LiveVod:
        async with self._gql_factory() as gql:
            payload = await gql.query(CHANNEL_LIVE_VOD_QUERY, {"login": channel_login})
        return live_vod_from_gql(payload, channel_login)
