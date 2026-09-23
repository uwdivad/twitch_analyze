from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.core.json import dumps
from app.workers.clickhouse_consumer import chat_message_from_record


def test_chat_message_from_record_parses_valid_payload() -> None:
    payload = {
        "message_id": "message-1",
        "channel_id": "channel-1",
        "channel_login": "example",
        "channel_display_name": "Example",
        "session_id": "channel-1:2026-04-28",
        "session_date": date(2026, 4, 28).isoformat(),
        "chatter_user_id": "user-1",
        "chatter_login": "viewer",
        "chatter_display_name": "Viewer",
        "message_text": "hello",
        "event_ts": datetime(2026, 4, 28, tzinfo=UTC).isoformat(),
    }
    record = SimpleNamespace(topic="twitch.chat.messages", partition=0, offset=1, value=dumps(payload).encode())

    message = chat_message_from_record(record)

    assert message is not None
    assert message.message_id == "message-1"
    assert message.message_text == "hello"
    # Payloads produced before the `source` field existed default to live chat.
    assert message.source == "live"


def test_chat_message_from_record_preserves_vod_source() -> None:
    payload = {
        "message_id": "vod-comment-1",
        "channel_id": "channel-1",
        "channel_login": "example",
        "channel_display_name": "Example",
        "session_id": "vod:123",
        "session_date": date(2026, 4, 28).isoformat(),
        "chatter_user_id": "user-1",
        "chatter_login": "viewer",
        "chatter_display_name": "Viewer",
        "message_text": "KEKW",
        "event_ts": datetime(2026, 4, 28, 12, 5, tzinfo=UTC).isoformat(),
        "source": "vod",
    }
    record = SimpleNamespace(topic="twitch.chat.messages", partition=0, offset=3, value=dumps(payload).encode())

    message = chat_message_from_record(record)

    assert message is not None
    assert message.source == "vod"
    assert message.session_id == "vod:123"


def test_chat_message_from_record_skips_invalid_payload() -> None:
    record = SimpleNamespace(topic="twitch.chat.messages", partition=0, offset=2, value=b'{"message_id":"bad"}')

    assert chat_message_from_record(record) is None
