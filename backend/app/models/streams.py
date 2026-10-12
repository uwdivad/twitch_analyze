"""Pydantic models for the "is this streamer live?" lookup across platforms."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

StreamPlatform = Literal["twitch", "kick", "youtube"]
STREAM_PLATFORMS: tuple[StreamPlatform, ...] = ("twitch", "kick", "youtube")

# not_found: the platform has no channel with that handle.
# error: the platform could not be checked (blocked, timed out, changed its page).
StreamState = Literal["live", "offline", "not_found", "error"]


class PlatformStatus(BaseModel):
    platform: StreamPlatform
    # Normalized handle that was checked (lowercase login for Twitch/Kick, YouTube handle without '@').
    handle: str
    state: StreamState
    display_name: str = ""
    channel_url: str = ""
    avatar_url: str = ""
    # Live-only details; empty/None when offline.
    title: str = ""
    category: str = ""
    viewer_count: int | None = None
    started_at: datetime | None = None
    thumbnail_url: str = ""
    # YouTube: the live video id (what the embed needs) and the UC... channel id.
    video_id: str = ""
    channel_id: str = ""
    # Human-readable note for not_found/error (and "Scheduled stream" for YouTube premieres).
    detail: str = ""
    checked_at: datetime


class StreamStatusResponse(BaseModel):
    name: str
    checked_at: datetime
    # Always one entry per platform, in STREAM_PLATFORMS order.
    platforms: list[PlatformStatus]
