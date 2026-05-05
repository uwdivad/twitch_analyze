from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ChannelInfo(BaseModel):
    channel_id: str
    channel_login: str
    channel_display_name: str
    status: str = "configured"
    detail: str | None = None


class ChatMessage(BaseModel):
    message_id: str
    eventsub_message_id: str = ""
    channel_id: str
    channel_login: str
    channel_display_name: str
    session_id: str
    session_date: date
    chatter_user_id: str
    chatter_login: str
    chatter_display_name: str
    message_text: str
    message_fragments: list[dict[str, Any]] = Field(default_factory=list)
    badges: list[dict[str, Any]] = Field(default_factory=list)
    emotes: list[dict[str, Any]] = Field(default_factory=list)
    mentions: list[dict[str, Any]] = Field(default_factory=list)
    reply: dict[str, Any] | None = None
    raw_event: dict[str, Any] = Field(default_factory=dict)
    event_ts: datetime
    received_at: datetime = Field(default_factory=utc_now)


class LiveEnvelope(BaseModel):
    type: str
    payload: dict[str, Any] | ChatMessage | list[ChatMessage] | list[ChannelInfo]


class MessageQuery(BaseModel):
    channel: str | None = None
    session_id: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class VolumePoint(BaseModel):
    bucket: datetime
    message_count: int
    unique_chatter_count: int


class MessageTotal(BaseModel):
    count: int


class TopItem(BaseModel):
    value: str
    count: int
