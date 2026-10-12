"""Pydantic models for saving the tail of a channel's in-progress live VOD."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Only Twitch is wired up today; the field exists so other platforms can be added
# without changing the API shape.
ClipPlatform = Literal["twitch"]
ClipJobStatus = Literal["queued", "resolving", "downloading", "completed", "failed"]
ACTIVE_CLIP_STATUSES: frozenset[str] = frozenset({"queued", "resolving", "downloading"})


class CreateClipRequest(BaseModel):
    channel: str = Field(min_length=1, max_length=256)
    platform: ClipPlatform = "twitch"
    duration_minutes: int = Field(default=5, ge=1, le=60)


class LiveVod(BaseModel):
    """The archive VOD a platform is currently recording for a live channel."""

    platform: str
    video_id: str
    title: str
    channel_id: str
    channel_login: str
    channel_display_name: str
    created_at: datetime
    # Length reported by the platform's metadata; the playlist is the ground truth.
    duration_seconds: int = 0


class ClipJob(BaseModel):
    job_id: str
    platform: str
    channel_login: str
    channel_display_name: str = ""
    requested_seconds: int
    status: ClipJobStatus
    started_at: datetime
    updated_at: datetime
    video_id: str = ""
    video_title: str = ""
    video_created_at: datetime | None = None
    # Window of the VOD the clip covers, as offsets from the VOD start.
    clip_start_seconds: float = 0.0
    clip_end_seconds: float = 0.0
    clip_duration_seconds: float = 0.0
    # ffmpeg progress while downloading.
    downloaded_seconds: float = 0.0
    file_name: str = ""
    file_size_bytes: int = 0
    detail: str = ""
    error: str = ""
