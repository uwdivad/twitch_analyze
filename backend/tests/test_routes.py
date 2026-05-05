from datetime import UTC, date, datetime

import pytest

from app.api.routes import message_total, recent_messages
from app.models.chat import ChatMessage


class FailingClickHouse:
    async def recent_messages(self, **_kwargs):
        raise RuntimeError("query failed")

    async def message_total(self, **_kwargs):
        raise RuntimeError("query failed")


class CountingClickHouse:
    async def message_total(self, **kwargs):
        assert kwargs == {"channel": "example", "session_id": None}
        return 42


class FakeHub:
    def recent(self, channel=None, limit=100):
        return [
            ChatMessage(
                message_id="message-1",
                channel_id="channel-1",
                channel_login=channel or "example",
                channel_display_name="Example",
                session_id="channel-1:2026-04-28",
                session_date=date(2026, 4, 28),
                chatter_user_id="user-1",
                chatter_login="viewer",
                chatter_display_name="Viewer",
                message_text="hello",
                event_ts=datetime(2026, 4, 28, tzinfo=UTC),
            )
        ][:limit]


@pytest.mark.anyio
async def test_recent_messages_falls_back_to_hub_when_clickhouse_fails() -> None:
    messages = await recent_messages(
        channel="example",
        session_id=None,
        limit=150,
        clickhouse=FailingClickHouse(),
        hub=FakeHub(),
    )

    assert len(messages) == 1
    assert messages[0].message_text == "hello"


@pytest.mark.anyio
async def test_message_total_returns_clickhouse_count() -> None:
    total = await message_total(
        channel="example",
        session_id=None,
        clickhouse=CountingClickHouse(),
    )

    assert total.count == 42


@pytest.mark.anyio
async def test_message_total_returns_zero_when_clickhouse_fails() -> None:
    total = await message_total(
        channel="example",
        session_id=None,
        clickhouse=FailingClickHouse(),
    )

    assert total.count == 0
