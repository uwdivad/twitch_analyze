from datetime import date

from app.ingestion.twitch import TwitchEventSubClient
from app.models.chat import ChannelInfo


def test_normalizes_channel_chat_message_payload() -> None:
    async def noop_message(_message):
        return None

    async def noop_status(_status):
        return None

    client = TwitchEventSubClient(
        client_id="client",
        access_token="token",
        channels=["example"],
        user_id="user-1",
        on_message=noop_message,
        on_status=noop_status,
    )
    client._channels_by_id = {
        "channel-1": ChannelInfo(
            channel_id="channel-1",
            channel_login="example",
            channel_display_name="Example",
        )
    }

    message = client._normalize_message(
        {
            "metadata": {
                "message_id": "eventsub-1",
                "message_type": "notification",
                "message_timestamp": "2026-04-28T10:15:30.123Z",
            },
            "payload": {
                "subscription": {"type": "channel.chat.message"},
                "event": {
                    "broadcaster_user_id": "channel-1",
                    "broadcaster_user_login": "example",
                    "broadcaster_user_name": "Example",
                    "chatter_user_id": "chatter-1",
                    "chatter_user_login": "viewer",
                    "chatter_user_name": "Viewer",
                    "message_id": "message-1",
                    "message": {
                        "text": "hello Kappa",
                        "fragments": [
                            {"type": "text", "text": "hello "},
                            {"type": "emote", "text": "Kappa", "emote": {"id": "25"}},
                        ],
                    },
                    "badges": [{"set_id": "subscriber", "id": "12"}],
                    "reply": None,
                },
            },
        }
    )

    assert message.message_id == "message-1"
    assert message.eventsub_message_id == "eventsub-1"
    assert message.channel_login == "example"
    assert message.session_id == "channel-1:2026-04-28"
    assert message.session_date == date(2026, 4, 28)
    assert message.chatter_login == "viewer"
    assert message.message_text == "hello Kappa"
    assert message.emotes[0]["text"] == "Kappa"
