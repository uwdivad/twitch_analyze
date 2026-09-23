"""Pydantic models for VOD chat-replay fetching and chat-peak analysis."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.chat import TopItem

VodJobStatus = Literal["queued", "fetching", "ingesting", "analyzing", "completed", "failed"]
ACTIVE_VOD_STATUSES: frozenset[str] = frozenset({"queued", "fetching", "ingesting", "analyzing"})


class VodMetadata(BaseModel):
    """VOD metadata returned by the Twitch GQL video query."""

    video_id: str
    title: str
    duration_seconds: int
    created_at: datetime
    channel_id: str
    channel_login: str
    channel_display_name: str


class AnalyzeVodRequest(BaseModel):
    video: str = Field(min_length=1, max_length=512)
    force: bool = False


class VodAnalysisJob(BaseModel):
    video_id: str
    status: VodJobStatus
    started_at: datetime
    updated_at: datetime
    pages_fetched: int = 0
    fetched_comments: int = 0
    stored_comments: int = 0
    duration_seconds: int = 0
    last_offset_seconds: int = 0
    detail: str = ""
    error: str = ""


class VodPeak(BaseModel):
    peak_id: int
    start_seconds: int
    end_seconds: int
    peak_seconds: int
    message_count: int
    peak_bucket_count: int
    messages_per_second: float
    score: float
    baseline: float
    top_emotes: list[TopItem] = Field(default_factory=list)
    top_tokens: list[TopItem] = Field(default_factory=list)
    # "name: text" strings, at most 20.
    sample_messages: list[str] = Field(default_factory=list)
    # Heuristic label, always set.
    label: str
    # Optional LLM-generated title.
    title: str = ""


class VodAnalysis(BaseModel):
    video_id: str
    channel_id: str
    channel_login: str
    channel_display_name: str
    title: str
    video_created_at: datetime
    duration_seconds: int
    bucket_seconds: int
    message_count: int
    unique_chatter_count: int
    status: str
    error: str = ""
    peaks: list[VodPeak] = Field(default_factory=list)
    label_model: str = ""
    analyzed_at: datetime
    updated_at: datetime


class VodAnalysisResponse(BaseModel):
    job: VodAnalysisJob | None = None
    analysis: VodAnalysis | None = None


class VodActivityBucket(BaseModel):
    index: int
    offset_seconds: int
    message_count: int
    unique_chatter_count: int


class VodActivity(BaseModel):
    video_id: str
    bucket_seconds: int
    duration_seconds: int
    buckets: list[VodActivityBucket]
