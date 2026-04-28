from datetime import date

from app.ingestion.irc import TwitchIrcClient, parse_badges, parse_irc_tags


def test_parse_irc_tags_unescapes_values() -> None:
    tags = parse_irc_tags(r"display-name=Some\sUser;reply-parent-msg-body=hello\sworld")

    assert tags["display-name"] == "Some User"
    assert tags["reply-parent-msg-body"] == "hello world"


def test_parse_badges() -> None:
    assert parse_badges("subscriber/12;bad") == [{"set_id": "subscriber", "id": "12;bad"}]
    assert parse_badges("subscriber/12,moderator/1") == [
        {"set_id": "subscriber", "id": "12"},
        {"set_id": "moderator", "id": "1"},
    ]


def test_normalizes_irc_privmsg_with_tags() -> None:
    async def noop_message(_message):
        return None

    async def noop_status(_status):
        return None

    client = TwitchIrcClient(
        channels=["example"],
        on_message=noop_message,
        on_status=noop_status,
        username="",
        access_token="",
    )

    message = client._normalize_privmsg(
        "@badge-info=;badges=subscriber/12;color=#1E90FF;display-name=Viewer;"
        "emotes=25:6-10;id=message-1;mod=0;room-id=channel-1;subscriber=1;"
        "tmi-sent-ts=1777371330123;turbo=0;user-id=chatter-1;user-type= "
        ":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #example :hello Kappa"
    )

    assert message is not None
    assert message.message_id == "message-1"
    assert message.channel_id == "channel-1"
    assert message.channel_login == "example"
    assert message.session_id == "channel-1:2026-04-28"
    assert message.session_date == date(2026, 4, 28)
    assert message.chatter_user_id == "chatter-1"
    assert message.chatter_login == "viewer"
    assert message.chatter_display_name == "Viewer"
    assert message.message_text == "hello Kappa"
    assert message.badges == [{"set_id": "subscriber", "id": "12"}]
    assert message.emotes[0]["text"] == "Kappa"
    assert message.raw_event["source"] == "twitch_irc"
