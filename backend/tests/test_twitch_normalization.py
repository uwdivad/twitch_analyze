import copy
from datetime import UTC, date, datetime
from typing import Any

from app.ingestion.twitch import TwitchEventSubClient
from app.models.chat import ChannelInfo


def _client() -> TwitchEventSubClient:
    async def noop_message(_message):
        return None

    async def noop_status(_status):
        return None

    return TwitchEventSubClient(
        client_id="client",
        access_token="token",
        channels=["example"],
        user_id="user-1",
        on_message=noop_message,
        on_status=noop_status,
    )


def _payload() -> dict[str, Any]:
    return {
        "metadata": {
            "message_id": "eventsub-1",
            "message_type": "notification",
            "message_timestamp": "2026-04-28T10:15:30.123Z",
        },
        "payload": {
            "subscription": {"type": "channel.chat.message"},
            "event": {
                "broadcaster_user_id": "channel-1",
                # Deliberately different from the resolved channel below, so the
                # test proves which source wins.
                "broadcaster_user_login": "event_login",
                "broadcaster_user_name": "Event Name",
                "chatter_user_id": "chatter-1",
                "chatter_user_login": "viewer",
                "chatter_user_name": "Viewer",
                "message_id": "message-1",
                "message": {
                    "text": "hello Kappa @friend",
                    "fragments": [
                        {"type": "text", "text": "hello "},
                        {"type": "emote", "text": "Kappa", "emote": {"id": "25"}},
                        {"type": "text", "text": " "},
                        {"type": "mention", "text": "@friend", "mention": {"user_login": "friend"}},
                    ],
                },
                "badges": [{"set_id": "subscriber", "id": "12", "info": "14"}],
                "reply": None,
            },
        },
    }


def test_normalizes_channel_chat_message_payload() -> None:
    client = _client()
    client._channels_by_id = {
        "channel-1": ChannelInfo(
            channel_id="channel-1",
            channel_login="resolved_login",
            channel_display_name="Resolved Name",
        )
    }
    payload = _payload()
    original = copy.deepcopy(payload)

    message = client._normalize_message(payload)

    assert message.message_id == "message-1"
    assert message.eventsub_message_id == "eventsub-1"
    # The channel resolved via Helix at startup wins over the event's broadcaster fields.
    assert message.channel_id == "channel-1"
    assert message.channel_login == "resolved_login"
    assert message.channel_display_name == "Resolved Name"
    assert message.event_ts == datetime(2026, 4, 28, 10, 15, 30, 123000, tzinfo=UTC)
    assert message.session_id == "channel-1:2026-04-28"
    assert message.session_date == date(2026, 4, 28)
    assert message.chatter_user_id == "chatter-1"
    assert message.chatter_login == "viewer"
    assert message.chatter_display_name == "Viewer"
    assert message.message_text == "hello Kappa @friend"
    assert message.message_fragments == original["payload"]["event"]["message"]["fragments"]
    assert message.emotes == [{"type": "emote", "text": "Kappa", "emote": {"id": "25"}}]
    assert message.mentions == [{"type": "mention", "text": "@friend", "mention": {"user_login": "friend"}}]
    assert message.badges == [{"set_id": "subscriber", "id": "12", "info": "14"}]
    assert message.reply is None
    assert message.raw_event == original
    assert message.source == "live"


def test_unknown_broadcaster_falls_back_to_event_fields() -> None:
    client = _client()
    client._channels_by_id = {}

    message = client._normalize_message(_payload())

    assert message.channel_id == "channel-1"
    assert message.channel_login == "event_login"
    assert message.channel_display_name == "Event Name"
    assert message.session_id == "channel-1:2026-04-28"
