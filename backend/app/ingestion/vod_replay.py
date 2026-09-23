"""Twitch VOD chat-replay fetcher.

Twitch has no official endpoint for VOD chat. This module reads replay comments
from Twitch's own web GQL API (the same source TwitchDownloader and
chat-downloader use). That API is undocumented and may change, so the URL,
Client-ID and persisted-query hash are passed in from settings, and everything
that depends on the GQL shape stays in this module.
"""

import asyncio
import json
import logging
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from urllib.parse import urlparse

import aiohttp

from app.ingestion.twitch import parse_twitch_timestamp
from app.models.chat import ChatMessage
from app.models.vod import VodMetadata

logger = logging.getLogger(__name__)

__all__ = [
    "COMMENTS_OPERATION_NAME",
    "GqlHttpError",
    "TwitchGqlClient",
    "VIDEO_QUERY",
    "normalize_comment",
    "parse_vod_reference",
    "video_from_gql",
]

COMMENTS_OPERATION_NAME = "VideoCommentsByOffsetOrCursor"
VIDEO_QUERY = (
    "query($id: ID!) { video(id: $id) { id title lengthSeconds createdAt "
    "owner { id login displayName } } }"
)
PERSISTED_QUERY_NOT_FOUND = "PersistedQueryNotFound"
MAX_BACKOFF_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 30
# Consecutive pages whose max contentOffsetSeconds does not advance before paging gives up.
# Pages hold ~60 comments, so 25 pages (~1500 comments in one second) means a stuck cursor,
# not a real chat burst.
MAX_STALLED_PAGES = 25
# GQL-level errors Twitch returns with HTTP 200 that are worth retrying (lowercase substrings).
TRANSIENT_GQL_ERRORS = ("service timeout", "service error", "service unavailable")
INTEGRITY_CHECK_ERROR = "failed integrity check"

# Indirection so tests can patch sleeps in this module without touching asyncio globally.
_sleep = asyncio.sleep

_BARE_ID_RE = re.compile(r"^v?(\d{1,20})$", re.IGNORECASE)
_VIDEOS_PATH_RE = re.compile(r"^/videos/(\d{1,20})/?$")
_LEGACY_PATH_RE = re.compile(r"^/[^/]+/v(?:ideo)?/(\d{1,20})/?$")

FetchJson = Callable[[list[Any] | dict[str, Any]], Awaitable[Any]]


class GqlHttpError(Exception):
    """Non-2xx HTTP response from the GQL endpoint.

    Raised by the transport (the default aiohttp one or an injected
    ``fetch_json``); ``TwitchGqlClient._post`` retries 429 and 5xx.
    """

    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"{status} {text}")
        self.status = status
        self.text = text


def parse_vod_reference(value: str) -> str:
    """Return the numeric video id from a bare id, ``v``-prefixed id or Twitch URL."""
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError("VOD reference is empty")

    bare = _BARE_ID_RE.match(candidate)
    if bare:
        return bare.group(1)

    to_parse = candidate if "://" in candidate else f"https://{candidate}"
    parsed = urlparse(to_parse)
    host = (parsed.hostname or "").lower()
    if host != "twitch.tv" and not host.endswith(".twitch.tv"):
        raise ValueError(f"not a Twitch VOD reference: {value!r}")

    for pattern in (_VIDEOS_PATH_RE, _LEGACY_PATH_RE):
        match = pattern.match(parsed.path)
        if match:
            return match.group(1)
    raise ValueError(f"no video id in Twitch URL: {value!r}")


def video_from_gql(payload: dict[str, Any]) -> VodMetadata:
    """Build ``VodMetadata`` from the raw video query response."""
    data = (payload or {}).get("data") or {}
    video = data.get("video")
    if not video:
        raise ValueError("VOD not found or unavailable")
    owner = video.get("owner") or {}
    return VodMetadata(
        video_id=str(video.get("id") or ""),
        title=video.get("title") or "",
        duration_seconds=int(video.get("lengthSeconds") or 0),
        created_at=parse_twitch_timestamp(video.get("createdAt")),
        channel_id=str(owner.get("id") or ""),
        channel_login=owner.get("login") or "",
        channel_display_name=owner.get("displayName") or owner.get("login") or "",
    )


def normalize_comment(node: dict[str, Any], video: VodMetadata) -> ChatMessage | None:
    """Convert one ``VideoCommentsByOffsetOrCursor`` comment node to a ``ChatMessage``.

    Returns ``None`` when the node lacks an id or content offset.
    """
    message_id = node.get("id")
    offset = node.get("contentOffsetSeconds")
    if not message_id or offset is None:
        return None
    try:
        offset_seconds = float(offset)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(offset_seconds):
        return None

    commenter = node.get("commenter")
    if commenter:
        chatter_user_id = str(commenter.get("id") or "")
        chatter_login = commenter.get("login") or ""
        chatter_display_name = commenter.get("displayName") or chatter_login
    else:
        chatter_user_id, chatter_login, chatter_display_name = "", "", "[deleted]"

    message = node.get("message") or {}
    fragments: list[dict[str, Any]] = []
    emotes: list[dict[str, Any]] = []
    text_parts: list[str] = []
    position = 0
    for fragment in message.get("fragments") or []:
        text = fragment.get("text") or ""
        text_parts.append(text)
        emote = fragment.get("emote")
        if emote:
            # Fragment ``id`` is "<emoteID>;<position>"; prefer ``emoteID``.
            emote_id = str(emote.get("emoteID") or str(emote.get("id") or "").split(";")[0])
            fragments.append({"type": "emote", "text": text, "emote": {"id": emote_id}})
            # Same shape as irc.parse_emotes; position is inclusive char range in message_text.
            emotes.append(
                {
                    "type": "emote",
                    "text": text,
                    "emote": {"id": emote_id},
                    "position": {"start": position, "end": position + len(text) - 1},
                }
            )
        else:
            fragments.append({"type": "text", "text": text})
        position += len(text)

    badges = [
        {"set_id": badge.get("setID") or "", "id": badge.get("version") or ""}
        for badge in message.get("userBadges") or []
        if badge and badge.get("setID")
    ]

    return ChatMessage(
        message_id=str(message_id),
        channel_id=video.channel_id,
        channel_login=video.channel_login,
        channel_display_name=video.channel_display_name,
        session_id=f"vod:{video.video_id}",
        session_date=video.created_at.date(),
        chatter_user_id=chatter_user_id,
        chatter_login=chatter_login,
        chatter_display_name=chatter_display_name,
        message_text="".join(text_parts),
        message_fragments=fragments,
        badges=badges,
        emotes=emotes,
        raw_event={
            "source": "twitch_vod",
            "video_id": video.video_id,
            "content_offset_seconds": offset,
            "node": node,
        },
        event_ts=video.created_at + timedelta(seconds=offset_seconds),
        received_at=datetime.now(UTC),
        source="vod",
    )


def _gql_error_messages(response: Any) -> list[str]:
    items = response if isinstance(response, list) else [response]
    messages: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for error in item.get("errors") or []:
            if isinstance(error, dict):
                messages.append(str(error.get("message") or error))
            else:
                messages.append(str(error))
    return messages


def _max_offset(edges: list[Any]) -> float | None:
    best: float | None = None
    for edge in edges:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        try:
            offset = float(node.get("contentOffsetSeconds"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(offset) and (best is None or offset > best):
            best = offset
    return best


class TwitchGqlClient:
    """Async client for Twitch's web GQL API, scoped to VOD metadata and chat replay."""

    def __init__(
        self,
        *,
        url: str,
        client_id: str,
        comments_query_hash: str,
        max_retries: int = 5,
        page_delay_seconds: float = 0.1,
        fetch_json: FetchJson | None = None,
    ) -> None:
        self.url = url
        self.client_id = client_id
        self.comments_query_hash = comments_query_hash
        self.max_retries = max(0, max_retries)
        self.page_delay_seconds = page_delay_seconds
        self._injected_fetch = fetch_json
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "TwitchGqlClient":
        if self._injected_fetch is None and self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"Client-Id": self.client_id, "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_video(self, video_id: str) -> VodMetadata:
        response = await self._post({"query": VIDEO_QUERY, "variables": {"id": video_id}})
        if isinstance(response, list):
            response = response[0] if response else {}
        return video_from_gql(response)

    async def iter_comment_pages(self, video_id: str) -> AsyncIterator[list[dict[str, Any]]]:
        """Yield pages of raw comment nodes in replay order.

        Only ids from the previous page are kept for dedup (pages overlap at the
        boundary), so memory stays O(page size).
        """
        variables: dict[str, Any] = {"videoID": video_id, "contentOffsetSeconds": 0}
        previous_ids: set[str] = set()
        empty_streak = 0
        stalled_pages = 0
        best_offset: float | None = None
        first = True
        while True:
            if not first and self.page_delay_seconds > 0:
                await _sleep(self.page_delay_seconds)
            first = False

            response = await self._post([self._comments_body(variables)])
            item = response[0] if isinstance(response, list) and response else response
            if not isinstance(item, dict):
                return
            video = (item.get("data") or {}).get("video")
            comments = video.get("comments") if isinstance(video, dict) else None
            if not comments:
                return

            edges = comments.get("edges") or []
            page_ids: set[str] = set()
            new_nodes: list[dict[str, Any]] = []
            for edge in edges:
                node = (edge or {}).get("node")
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if node_id:
                    page_ids.add(node_id)
                    if node_id in previous_ids:
                        continue
                new_nodes.append(node)
            previous_ids = page_ids

            page_max_offset = _max_offset(edges)
            if page_max_offset is not None and (best_offset is None or page_max_offset > best_offset):
                best_offset = page_max_offset
                stalled_pages = 0
            else:
                stalled_pages += 1

            if new_nodes:
                empty_streak = 0
                yield new_nodes
            else:
                empty_streak += 1
                if empty_streak >= 2:
                    logger.warning("VOD %s: two pages with no new comments; stopping", video_id)
                    return
            if stalled_pages >= MAX_STALLED_PAGES:
                logger.warning(
                    "VOD %s: comment offset stuck at %s for %s pages; stopping",
                    video_id,
                    best_offset,
                    stalled_pages,
                )
                return

            has_next = bool((comments.get("pageInfo") or {}).get("hasNextPage"))
            cursor = next(
                (edge.get("cursor") for edge in reversed(edges) if isinstance(edge, dict) and edge.get("cursor")),
                None,
            )
            if not has_next or not cursor:
                return
            variables = {"videoID": video_id, "cursor": cursor}

    def _comments_body(self, variables: dict[str, Any]) -> dict[str, Any]:
        return {
            "operationName": COMMENTS_OPERATION_NAME,
            "variables": variables,
            "extensions": {
                "persistedQuery": {"version": 1, "sha256Hash": self.comments_query_hash}
            },
        }

    async def _post(self, body: list[Any] | dict[str, Any]) -> Any:
        attempt = 0
        while True:
            status: int | str
            try:
                response = await self._fetch_json(body)
            except GqlHttpError as exc:
                retryable = exc.status == 429 or exc.status >= 500
                status, text = exc.status, exc.text
                if not retryable:
                    raise RuntimeError(f"Twitch GQL request failed: {status} {text}") from exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                status, text = type(exc).__name__, str(exc)
            else:
                errors = _gql_error_messages(response)
                if PERSISTED_QUERY_NOT_FOUND in errors:
                    raise RuntimeError(
                        "Twitch GQL persisted query not found; set TWITCH_GQL_COMMENTS_QUERY_HASH"
                    )
                if not errors:
                    return response
                lowered = [error.lower() for error in errors]
                if any(INTEGRITY_CHECK_ERROR in error for error in lowered):
                    raise RuntimeError(
                        "Twitch rejected the request (integrity check); the web Client-Id/hash may need updating"
                    )
                if not any(marker in error for error in lowered for marker in TRANSIENT_GQL_ERRORS):
                    raise RuntimeError(f"Twitch GQL errors: {'; '.join(errors)}")
                # Transient server-side GQL error on HTTP 200: retry like a 5xx.
                status, text = "gql", "; ".join(errors)

            if attempt >= self.max_retries:
                raise RuntimeError(f"Twitch GQL request failed: {status} {text}")
            delay = min(2**attempt, MAX_BACKOFF_SECONDS)
            attempt += 1
            logger.warning(
                "Twitch GQL request failed (%s); retry %s/%s in %ss", status, attempt, self.max_retries, delay
            )
            await _sleep(delay)

    async def _fetch_json(self, body: list[Any] | dict[str, Any]) -> Any:
        if self._injected_fetch is not None:
            return await self._injected_fetch(body)
        if self._session is None:
            raise RuntimeError("TwitchGqlClient must be used as an async context manager")
        async with self._session.post(self.url, json=body) as response:
            if response.status >= 400:
                raise GqlHttpError(response.status, await response.text())
            try:
                return await response.json(content_type=None)
            except (json.JSONDecodeError, UnicodeDecodeError, aiohttp.ContentTypeError) as exc:
                # Retryable transport failure; must never escape as ValueError ("VOD not found").
                raise GqlHttpError(502, f"invalid JSON: {exc}") from exc
