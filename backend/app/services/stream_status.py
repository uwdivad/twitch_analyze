"""Is a streamer live right now on Twitch, Kick and YouTube?

All three checks are keyless so the Streams view works without registering apps:

- Twitch: the web GQL API already used for chat replay and clips
  (``user(login) { stream { ... } }``; ``stream`` is null when offline).
- Kick: the site's own JSON API, ``GET kick.com/api/v2/channels/<slug>``
  (``livestream`` is null when offline, 404 for unknown slugs). It sits behind
  Cloudflare and may answer 403 from some hosts; that surfaces as ``state="error"``.
- YouTube: ``GET youtube.com/@<handle>/live``. While live the page's canonical URL
  is the ``watch?v=`` URL and the player data carries ``"isLiveNow":true``; offline
  the canonical URL is the ``/channel/UC...`` page.

Every platform is checked independently and concurrently; a failure on one is
reported as that platform's ``error`` entry rather than failing the request.
Results are cached briefly per (platform, handle) because the dashboard re-polls.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from app.core.config import Settings
from app.ingestion.vod_replay import TwitchGqlClient
from app.models.streams import STREAM_PLATFORMS, PlatformStatus, StreamPlatform, StreamStatusResponse

__all__ = [
    "TWITCH_STREAM_QUERY",
    "StreamStatusService",
    "YouTubeLivePage",
    "default_gql_factory",
    "kick_status_from_json",
    "normalize_handle",
    "parse_youtube_live_page",
    "twitch_status_from_gql",
    "youtube_status_from_page",
]

logger = logging.getLogger(__name__)

TWITCH_STREAM_QUERY = (
    "query($login: String!) { user(login: $login) { id login displayName profileImageURL(width: 150) "
    "stream { id title viewersCount createdAt type game { name } } } }"
)
# Kick and YouTube answer differently (or not at all) to non-browser user agents.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Skips YouTube's EU consent interstitial, which would hide the player data.
YOUTUBE_COOKIES = {"CONSENT": "YES+cb", "SOCS": "CAI"}
# Cap on the HTML read from a YouTube page; the fields we need are in the first ~1 MB.
YOUTUBE_MAX_BYTES = 3_000_000

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PLATFORM_HOSTS: dict[str, tuple[str, ...]] = {
    "twitch": ("twitch.tv",),
    "kick": ("kick.com",),
    "youtube": ("youtube.com", "youtu.be"),
}
# YouTube path prefixes that precede the actual handle/name.
_YOUTUBE_PATH_PREFIXES = ("c", "user", "channel")

_CANONICAL_RE = re.compile(r'<link\s+rel="canonical"\s+href="([^"]+)"', re.IGNORECASE)
_WATCH_ID_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{6,20})")
_CHANNEL_PATH_RE = re.compile(r"/channel/(UC[A-Za-z0-9_-]{10,})")
_CHANNEL_ID_RE = re.compile(r'"channelId":"(UC[A-Za-z0-9_-]{10,})"')
_IS_LIVE_NOW_RE = re.compile(r'"isLiveNow":(true|false)')
_OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"', re.IGNORECASE)
_OG_IMAGE_RE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]*)"', re.IGNORECASE)
_OWNER_NAME_RE = re.compile(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"')
_VIEW_COUNT_RE = re.compile(r'"originalViewCount":"(\d+)"')
_START_TS_RE = re.compile(r'"startTimestamp":"([^"]+)"')
_AVATAR_RE = re.compile(r'"avatar":\{"thumbnails":\[\{"url":"([^"]+)"')


def normalize_handle(platform: StreamPlatform, raw: str) -> str:
    """Return the bare handle for ``platform`` from a name, ``@handle`` or channel URL."""
    candidate = (raw or "").strip()
    if not candidate:
        raise ValueError(f"{platform} handle is empty")
    if "/" in candidate:
        to_parse = candidate if "://" in candidate else f"https://{candidate}"
        parsed = urlparse(to_parse)
        host = (parsed.hostname or "").lower()
        allowed = _PLATFORM_HOSTS[platform]
        if not any(host == allowed_host or host.endswith(f".{allowed_host}") for allowed_host in allowed):
            raise ValueError(f"not a {platform} channel URL: {raw!r}")
        parts = [part for part in parsed.path.split("/") if part]
        if platform == "youtube" and len(parts) >= 2 and parts[0] in _YOUTUBE_PATH_PREFIXES:
            parts = parts[1:]
        if not parts:
            raise ValueError(f"no channel in {platform} URL: {raw!r}")
        candidate = parts[0]
    candidate = candidate.lstrip("@")
    if platform in ("twitch", "kick"):
        candidate = candidate.lower()
    if not _HANDLE_RE.match(candidate):
        raise ValueError(f"invalid {platform} handle: {raw!r}")
    return candidate


def _parse_timestamp(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Kick writes "YYYY-MM-DD HH:MM:SS" without a zone; it is UTC.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Twitch
# ---------------------------------------------------------------------------


def twitch_status_from_gql(payload: dict[str, Any], handle: str, checked_at: datetime) -> PlatformStatus:
    user = ((payload or {}).get("data") or {}).get("user")
    base = {"platform": "twitch", "handle": handle, "channel_url": f"https://twitch.tv/{handle}", "checked_at": checked_at}
    if not isinstance(user, dict):
        return PlatformStatus(state="not_found", detail=f"No Twitch channel named {handle}", **base)
    base["display_name"] = user.get("displayName") or user.get("login") or handle
    base["avatar_url"] = user.get("profileImageURL") or ""
    base["channel_id"] = str(user.get("id") or "")
    stream = user.get("stream")
    if not isinstance(stream, dict):
        return PlatformStatus(state="offline", **base)
    game = stream.get("game")
    category = (game.get("name") or "") if isinstance(game, dict) else ""
    return PlatformStatus(
        state="live",
        title=stream.get("title") or "",
        category=category,
        viewer_count=_as_int(stream.get("viewersCount")),
        started_at=_parse_timestamp(stream.get("createdAt")),
        **base,
    )


# ---------------------------------------------------------------------------
# Kick
# ---------------------------------------------------------------------------


def kick_status_from_json(payload: dict[str, Any], handle: str, checked_at: datetime) -> PlatformStatus:
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    slug = payload.get("slug") or handle
    base = {
        "platform": "kick",
        "handle": handle,
        "channel_url": f"https://kick.com/{slug}",
        "display_name": user.get("username") or slug,
        "avatar_url": user.get("profile_pic") or "",
        "channel_id": str(payload.get("id") or ""),
        "checked_at": checked_at,
    }
    if payload.get("is_banned"):
        return PlatformStatus(state="offline", detail="Channel is banned", **base)
    livestream = payload.get("livestream")
    if not isinstance(livestream, dict) or livestream.get("is_live") is False:
        return PlatformStatus(state="offline", **base)
    categories = livestream.get("categories")
    category = ""
    if isinstance(categories, list) and categories and isinstance(categories[0], dict):
        category = categories[0].get("name") or ""
    thumbnail = livestream.get("thumbnail")
    thumbnail_url = thumbnail.get("url") or "" if isinstance(thumbnail, dict) else (thumbnail or "")
    return PlatformStatus(
        state="live",
        title=livestream.get("session_title") or "",
        category=category,
        viewer_count=_as_int(livestream.get("viewer_count")),
        started_at=_parse_timestamp(livestream.get("created_at")),
        thumbnail_url=str(thumbnail_url),
        **base,
    )


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YouTubeLivePage:
    canonical_url: str = ""
    video_id: str = ""
    channel_id: str = ""
    # None when the page carries no player data (channel page while offline).
    is_live_now: bool | None = None
    title: str = ""
    owner_name: str = ""
    image_url: str = ""
    viewer_count: int | None = None
    started_at: datetime | None = None
    avatar_url: str = ""


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def _json_string(raw: str) -> str:
    """Decode the body of a JSON string literal (\\u0026, \\/ ...) scraped from page data."""
    if not raw:
        return ""
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def parse_youtube_live_page(page_html: str) -> YouTubeLivePage:
    """Pull the live/offline signals out of a ``/@handle/live`` page."""
    canonical = html.unescape(_first(_CANONICAL_RE, page_html))
    video_id = _first(_WATCH_ID_RE, canonical)
    channel_id = _first(_CHANNEL_PATH_RE, canonical) or _first(_CHANNEL_ID_RE, page_html)
    live_flag = _first(_IS_LIVE_NOW_RE, page_html)
    viewer_raw = _first(_VIEW_COUNT_RE, page_html)
    return YouTubeLivePage(
        canonical_url=canonical,
        video_id=video_id,
        channel_id=channel_id,
        is_live_now=None if not live_flag else live_flag == "true",
        title=html.unescape(_first(_OG_TITLE_RE, page_html)),
        owner_name=_json_string(_first(_OWNER_NAME_RE, page_html)),
        image_url=html.unescape(_first(_OG_IMAGE_RE, page_html)),
        viewer_count=int(viewer_raw) if viewer_raw else None,
        started_at=_parse_timestamp(_first(_START_TS_RE, page_html)),
        avatar_url=_json_string(_first(_AVATAR_RE, page_html)),
    )


def youtube_status_from_page(page: YouTubeLivePage, handle: str, checked_at: datetime) -> PlatformStatus:
    base = {
        "platform": "youtube",
        "handle": handle,
        "channel_url": f"https://www.youtube.com/@{handle}",
        "channel_id": page.channel_id,
        "checked_at": checked_at,
    }
    if page.video_id and page.is_live_now:
        return PlatformStatus(
            state="live",
            display_name=page.owner_name or handle,
            title=page.title,
            viewer_count=page.viewer_count,
            started_at=page.started_at,
            thumbnail_url=page.image_url,
            video_id=page.video_id,
            **base,
        )
    if page.video_id:
        # The /live URL resolved to a scheduled premiere/stream that has not started.
        return PlatformStatus(
            state="offline",
            display_name=page.owner_name or handle,
            title=page.title,
            thumbnail_url=page.image_url,
            video_id=page.video_id,
            detail="Scheduled stream",
            **base,
        )
    if not page.channel_id:
        return PlatformStatus(state="error", detail="Could not read the YouTube channel page", **base)
    # Offline: the canonical URL is the channel page, whose og tags describe the channel.
    return PlatformStatus(
        state="offline",
        display_name=page.title or handle,
        avatar_url=page.avatar_url or page.image_url,
        **base,
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

GqlFactory = Callable[[Settings], AbstractAsyncContextManager[Any]]
Fetcher = Callable[[str, dict[str, str] | None, dict[str, str] | None, float], Awaitable[tuple[int, str]]]


def default_gql_factory(settings: Settings) -> TwitchGqlClient:
    # One retry at most: this is an interactive status check, not a bulk import.
    return TwitchGqlClient(
        url=settings.twitch_gql_url,
        client_id=settings.twitch_gql_client_id,
        comments_query_hash=settings.twitch_gql_comments_query_hash,
        max_retries=1,
        page_delay_seconds=0,
    )


class StreamStatusService:
    """Checks live status per platform with a short per-handle cache.

    ``fetch_text`` and ``gql_factory`` exist for tests; the defaults use one shared
    aiohttp session (opened lazily, closed by ``aclose``) and the Twitch GQL client.
    """

    def __init__(
        self,
        *,
        gql_factory: GqlFactory = default_gql_factory,
        fetch_text: Fetcher | None = None,
    ) -> None:
        self._gql_factory = gql_factory
        self._injected_fetch = fetch_text
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[tuple[str, str], tuple[float, PlatformStatus]] = {}
        # Indirection so tests can control cache expiry.
        self._now: Callable[[], float] = time.monotonic

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def lookup(
        self,
        name: str,
        overrides: dict[str, str],
        settings: Settings,
    ) -> StreamStatusResponse:
        """Check every platform for ``name`` (or its per-platform override) concurrently."""
        if not (name or "").strip():
            raise ValueError("name is empty")
        checked_at = datetime.now(UTC)
        handles: dict[StreamPlatform, str | ValueError] = {}
        for platform in STREAM_PLATFORMS:
            raw = overrides.get(platform) or name
            try:
                handles[platform] = normalize_handle(platform, raw)
            except ValueError as exc:
                handles[platform] = exc
        if all(isinstance(value, ValueError) for value in handles.values()):
            raise ValueError(f"invalid streamer name: {name!r}")

        async def check(platform: StreamPlatform) -> PlatformStatus:
            handle = handles[platform]
            if isinstance(handle, ValueError):
                return PlatformStatus(
                    platform=platform,
                    handle=(overrides.get(platform) or name).strip(),
                    state="error",
                    detail=str(handle),
                    checked_at=checked_at,
                )
            return await self.status(platform, handle, settings)

        results = await asyncio.gather(*(check(platform) for platform in STREAM_PLATFORMS))
        return StreamStatusResponse(name=name.strip(), checked_at=checked_at, platforms=list(results))

    async def status(self, platform: StreamPlatform, handle: str, settings: Settings) -> PlatformStatus:
        """Cached status for one platform/handle; never raises."""
        key = (platform, handle)
        cached = self._cache.get(key)
        now = self._now()
        if cached is not None and cached[0] > now:
            return cached[1]
        checked_at = datetime.now(UTC)
        timeout = max(1.0, float(settings.stream_status_timeout_seconds))
        try:
            # Hard cap on the whole check (the GQL client retries once with backoff).
            result = await asyncio.wait_for(self._check(platform, handle, settings, checked_at), timeout * 2)
        except asyncio.TimeoutError:
            result = self._error(platform, handle, checked_at, f"{platform} did not answer within {timeout:g}s")
        except Exception as exc:  # noqa: BLE001 - one platform failing must not break the others
            logger.warning("%s status check for %r failed: %s", platform, handle, exc)
            result = self._error(platform, handle, checked_at, str(exc) or type(exc).__name__)
        ttl = max(0.0, float(settings.stream_status_cache_seconds))
        if ttl > 0:
            self._cache[key] = (now + ttl, result)
            self._prune_cache(now)
        return result

    def _prune_cache(self, now: float) -> None:
        expired = [key for key, (expires_at, _status) in self._cache.items() if expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)

    @staticmethod
    def _error(platform: StreamPlatform, handle: str, checked_at: datetime, detail: str) -> PlatformStatus:
        return PlatformStatus(platform=platform, handle=handle, state="error", detail=detail, checked_at=checked_at)

    async def _check(
        self, platform: StreamPlatform, handle: str, settings: Settings, checked_at: datetime
    ) -> PlatformStatus:
        if platform == "twitch":
            return await self._check_twitch(handle, settings, checked_at)
        if platform == "kick":
            return await self._check_kick(handle, settings, checked_at)
        return await self._check_youtube(handle, settings, checked_at)

    async def _check_twitch(self, handle: str, settings: Settings, checked_at: datetime) -> PlatformStatus:
        async with self._gql_factory(settings) as gql:
            payload = await gql.query(TWITCH_STREAM_QUERY, {"login": handle})
        return twitch_status_from_gql(payload, handle, checked_at)

    async def _check_kick(self, handle: str, settings: Settings, checked_at: datetime) -> PlatformStatus:
        url = f"{settings.kick_api_url.rstrip('/')}/{quote(handle, safe='')}"
        status, text = await self._fetch_text(
            url,
            {"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json"},
            None,
            float(settings.stream_status_timeout_seconds),
        )
        if status == 404:
            return PlatformStatus(
                platform="kick",
                handle=handle,
                state="not_found",
                channel_url=f"https://kick.com/{handle}",
                detail=f"No Kick channel named {handle}",
                checked_at=checked_at,
            )
        if status >= 400:
            blocked = " (blocked by Cloudflare)" if status in (403, 429) else ""
            raise RuntimeError(f"Kick API returned HTTP {status}{blocked}")
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise RuntimeError("Kick API returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Kick API returned an unexpected response shape")
        return kick_status_from_json(payload, handle, checked_at)

    async def _check_youtube(self, handle: str, settings: Settings, checked_at: datetime) -> PlatformStatus:
        base_url = settings.youtube_base_url.rstrip("/")
        headers = {"User-Agent": BROWSER_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
        timeout = float(settings.stream_status_timeout_seconds)
        status, text = await self._fetch_text(f"{base_url}/@{quote(handle, safe='')}/live", headers, YOUTUBE_COOKIES, timeout)
        if status == 404:
            # Legacy custom URLs (youtube.com/c/<name>) predate handles.
            status, text = await self._fetch_text(f"{base_url}/c/{quote(handle, safe='')}/live", headers, YOUTUBE_COOKIES, timeout)
        if status == 404:
            return PlatformStatus(
                platform="youtube",
                handle=handle,
                state="not_found",
                channel_url=f"https://www.youtube.com/@{handle}",
                detail=f"No YouTube channel named @{handle}",
                checked_at=checked_at,
            )
        if status >= 400:
            raise RuntimeError(f"YouTube returned HTTP {status}")
        return youtube_status_from_page(parse_youtube_live_page(text), handle, checked_at)

    async def _fetch_text(
        self,
        url: str,
        headers: dict[str, str] | None,
        cookies: dict[str, str] | None,
        timeout_seconds: float,
    ) -> tuple[int, str]:
        if self._injected_fetch is not None:
            return await self._injected_fetch(url, headers, cookies, timeout_seconds)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        async with self._session.get(
            url,
            headers=headers,
            cookies=cookies,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            allow_redirects=True,
        ) as response:
            # StreamReader.read(n) returns the next available chunk (often ~10 KB), not n
            # bytes, so keep reading until the cap or EOF.
            chunks: list[bytes] = []
            remaining = YOUTUBE_MAX_BYTES
            while remaining > 0:
                chunk = await response.content.read(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return response.status, b"".join(chunks).decode("utf-8", errors="replace")
