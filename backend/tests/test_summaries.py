from datetime import UTC, date, datetime

import pytest

from app.models.chat import ChatMessage, SpikeWindow, SummaryContext, SummarySourceStats, TopItem
from app.services.summaries import SummaryService


def chat_message(message_id: str, text: str) -> ChatMessage:
    return ChatMessage(
        message_id=message_id,
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        session_id="channel-1:2026-05-06",
        session_date=date(2026, 5, 6),
        chatter_user_id="user-1",
        chatter_login="viewer",
        chatter_display_name="Viewer",
        message_text=text,
        event_ts=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
    )


class EmptyClickHouse:
    async def summary_context(self, **_kwargs):
        return None


class CapturingClickHouse:
    def __init__(self) -> None:
        self.inserted = None

    async def summary_context(self, **kwargs):
        assert kwargs == {"channel": "example", "window_minutes": 60, "max_messages": 25}
        return SummaryContext(
            channel_id="channel-1",
            channel_login="example",
            channel_display_name="Example",
            session_id="channel-1:2026-05-06",
            window_start=datetime(2026, 5, 6, 11, 0, tzinfo=UTC),
            window_end=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
            window_size="60m",
            stats=SummarySourceStats(
                message_count=2,
                unique_chatter_count=1,
                top_chatters=[TopItem(value="viewer", count=2)],
                top_emotes=[],
                spike_windows=[
                    SpikeWindow(bucket=datetime(2026, 5, 6, 11, 30, tzinfo=UTC), message_count=2, unique_chatter_count=1)
                ],
                sampled_message_count=2,
            ),
            sample_messages=[chat_message("message-1", "hello"), chat_message("message-2", "great stream")],
        )

    async def insert_summary(self, summary):
        self.inserted = summary


@pytest.mark.anyio
async def test_summary_generation_rejects_missing_openai_key() -> None:
    service = SummaryService(clickhouse=EmptyClickHouse(), api_key="", model="test-model", max_messages=25)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await service.generate(channel="example", window_minutes=60)


@pytest.mark.anyio
async def test_summary_generation_stores_openai_result(monkeypatch) -> None:
    clickhouse = CapturingClickHouse()
    service = SummaryService(clickhouse=clickhouse, api_key="test-key", model="test-model", max_messages=25)
    monkeypatch.setattr(service, "_call_openai", lambda _context: "### Short recap\nChat talked about the stream.")

    summary = await service.generate(channel="example", window_minutes=60)

    assert summary.summary_text == "### Short recap\nChat talked about the stream."
    assert summary.source_stats.message_count == 2
    assert summary.source_stats.spike_windows[0].bucket == datetime(2026, 5, 6, 11, 30, tzinfo=UTC)
    assert summary.model == "test-model"
    assert clickhouse.inserted == summary


@pytest.mark.anyio
async def test_summary_generation_rejects_empty_window() -> None:
    service = SummaryService(clickhouse=EmptyClickHouse(), api_key="test-key", model="test-model", max_messages=25)

    with pytest.raises(ValueError, match="No chat messages"):
        await service.generate(channel="example", window_minutes=60)
