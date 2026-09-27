import json
from datetime import UTC, date, datetime

import pytest

from app.models.chat import ChatMessage
from app.storage.realtime import RealtimeHub


def _message(index: int, channel_login: str = "example") -> ChatMessage:
    return ChatMessage(
        message_id=f"message-{index}",
        channel_id=f"{channel_login}-id",
        channel_login=channel_login,
        channel_display_name=channel_login.title(),
        session_id=f"{channel_login}-id:2026-04-28",
        session_date=date(2026, 4, 28),
        chatter_user_id="user-1",
        chatter_login="viewer",
        chatter_display_name="Viewer",
        message_text=f"hello {index}",
        event_ts=datetime(2026, 4, 28, 12, 0, index, tzinfo=UTC),
        received_at=datetime(2026, 4, 28, 12, 0, index, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_overflowed_subscriber_gets_close_sentinel_and_is_dropped() -> None:
    hub = RealtimeHub(recent_limit=10)
    queue = await hub.subscribe()

    while not queue.full():
        queue.put_nowait("backlog")

    await hub.broadcast_status({"status": "overflow"})

    # The stale queue is drained and left with only the close sentinel.
    assert queue.get_nowait() is None
    assert queue.empty()

    # The subscriber is no longer part of the hub: further broadcasts skip it.
    await hub.broadcast_status({"status": "again"})
    assert queue.empty()


@pytest.mark.anyio
async def test_healthy_subscriber_still_receives_broadcasts() -> None:
    hub = RealtimeHub(recent_limit=10)
    queue = await hub.subscribe()

    await hub.broadcast_status({"status": "ok"})

    data = queue.get_nowait()
    assert data is not None
    assert json.loads(data) == {"type": "status", "payload": {"status": "ok"}}
    assert queue.empty()


@pytest.mark.anyio
async def test_publish_message_broadcasts_chat_envelope() -> None:
    hub = RealtimeHub(recent_limit=10)
    queue = await hub.subscribe()
    message = _message(1)

    await hub.publish_message(message)

    data = queue.get_nowait()
    assert data is not None
    decoded = json.loads(data)
    assert decoded["type"] == "chat_message"
    assert ChatMessage.model_validate(decoded["payload"]) == message
    assert hub.recent() == [message]


@pytest.mark.anyio
async def test_recent_is_bounded_to_recent_limit_and_keeps_newest() -> None:
    hub = RealtimeHub(recent_limit=3)
    messages = [_message(index) for index in range(5)]

    for message in messages:
        await hub.publish_message(message)

    assert hub.recent() == messages[2:]
    # ``limit`` keeps the newest messages, oldest first.
    assert hub.recent(limit=2) == messages[3:]


@pytest.mark.anyio
async def test_recent_filters_by_channel_case_insensitively() -> None:
    hub = RealtimeHub(recent_limit=10)
    first = _message(1, "alpha")
    other = _message(2, "beta")
    second = _message(3, "alpha")
    third = _message(4, "alpha")
    for message in (first, other, second, third):
        await hub.publish_message(message)

    assert hub.recent(channel="ALPHA") == [first, second, third]
    assert hub.recent(channel="beta") == [other]
    assert hub.recent(channel="missing") == []
    # The limit applies after the channel filter.
    assert hub.recent(channel="alpha", limit=2) == [second, third]
    assert hub.recent(limit=2) == [second, third]
