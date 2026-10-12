import hashlib
from datetime import UTC, date, datetime

from app.ingestion.irc import TwitchIrcClient, parse_badges, parse_irc_tags


def _client() -> TwitchIrcClient:
    async def noop_message(_message):
        return None

    async def noop_status(_status):
        return None

    return TwitchIrcClient(
        channels=["example"],
        on_message=noop_message,
        on_status=noop_status,
        username="",
        access_token="",
    )


def test_parse_irc_tags_unescapes_values() -> None:
    tags = parse_irc_tags(r"display-name=Some\sUser;reply-parent-msg-body=hello\sworld")

    assert tags["display-name"] == "Some User"
    assert tags["reply-parent-msg-body"] == "hello world"


def test_parse_irc_tags_escape_edge_cases() -> None:
    # Escapes are resolved in a single left-to-right pass: `\\s` is an escaped
    # backslash followed by a literal "s", not a backslash followed by a space.
    assert parse_irc_tags(r"a=\\s") == {"a": "\\s"}
    assert parse_irc_tags(r"a=one\:two") == {"a": "one;two"}
    assert parse_irc_tags(r"a=\r\n") == {"a": "\r\n"}
    # A lone trailing backslash is dropped.
    assert parse_irc_tags("a=trail\\") == {"a": "trail"}
    # Unknown escapes yield the escaped character verbatim.
    assert parse_irc_tags(r"a=\q\x") == {"a": "qx"}
    # Keys without a value (and empty values) map to "".
    assert parse_irc_tags("flag;empty=") == {"flag": "", "empty": ""}
    assert parse_irc_tags("") == {}


def test_parse_badges() -> None:
    assert parse_badges("") == []
    assert parse_badges("subscriber/12,moderator/1") == [
        {"set_id": "subscriber", "id": "12"},
        {"set_id": "moderator", "id": "1"},
    ]
    # A badge without a version keeps an empty id; empty entries are skipped.
    assert parse_badges("premium,,/3,vip/1") == [
        {"set_id": "premium", "id": ""},
        {"set_id": "vip", "id": "1"},
    ]


def test_normalizes_irc_privmsg_with_tags() -> None:
    client = _client()

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
    assert message.event_ts == datetime(2026, 4, 28, 10, 15, 30, 123000, tzinfo=UTC)
    assert message.session_id == "channel-1:2026-04-28"
    assert message.session_date == date(2026, 4, 28)
    assert message.chatter_user_id == "chatter-1"
    assert message.chatter_login == "viewer"
    assert message.chatter_display_name == "Viewer"
    assert message.message_text == "hello Kappa"
    assert message.badges == [{"set_id": "subscriber", "id": "12"}]
    assert message.emotes == [
        {"type": "emote", "text": "Kappa", "emote": {"id": "25"}, "position": {"start": 6, "end": 10}}
    ]
    assert message.reply is None
    assert message.raw_event["source"] == "twitch_irc"
    assert message.raw_event["tags"]["room-id"] == "channel-1"


def test_normalizes_reply_tags() -> None:
    client = _client()

    message = client._normalize_privmsg(
        "@display-name=Viewer;id=message-2;room-id=channel-1;tmi-sent-ts=1777371330123;user-id=chatter-1;"
        r"reply-parent-msg-id=parent-1;reply-parent-user-id=user-9;reply-parent-user-login=streamer;"
        r"reply-parent-display-name=Streamer;reply-parent-msg-body=first\sline\:ok;"
        "reply-thread-parent-msg-id=thread-1;reply-thread-parent-user-login=starter "
        ":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #example :@Streamer agreed"
    )

    assert message is not None
    assert message.reply == {
        "parent_message_id": "parent-1",
        "parent_user_id": "user-9",
        "parent_user_login": "streamer",
        "parent_display_name": "Streamer",
        "parent_message_body": "first line;ok",
        "thread_parent_message_id": "thread-1",
        "thread_parent_user_login": "starter",
    }


def test_normalizes_privmsg_without_tags_using_fallbacks() -> None:
    client = _client()
    line = ":Viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #Example :no tags here"
    before = datetime.now(UTC)

    message = client._normalize_privmsg(line)

    after = datetime.now(UTC)
    assert message is not None
    # Without room-id the channel login doubles as the channel id.
    assert message.channel_id == "example"
    assert message.channel_login == "example"
    assert message.session_id == f"example:{message.event_ts.date().isoformat()}"
    # Without tmi-sent-ts the receive time is used.
    assert before <= message.event_ts <= after
    # Without an id tag a deterministic id is derived from the channel, time and raw line.
    expected_id = (
        f"irc:example:{int(message.event_ts.timestamp() * 1000)}:"
        f"{hashlib.sha1(line.encode('utf-8')).hexdigest()}"
    )
    assert message.message_id == expected_id
    assert message.chatter_user_id == ""
    assert message.chatter_login == "viewer"
    assert message.chatter_display_name == "viewer"
    assert message.message_text == "no tags here"
    assert message.badges == []
    assert message.emotes == []
    assert message.reply is None
    assert message.raw_event == {"source": "twitch_irc", "line": line, "tags": {}}


def test_non_privmsg_lines_are_ignored() -> None:
    client = _client()

    assert client._normalize_privmsg(":tmi.twitch.tv 001 justinfan123 :Welcome, GLHF!") is None
    assert client._normalize_privmsg("@room-id=channel-1;tmi-sent-ts=1 :tmi.twitch.tv ROOMSTATE #example") is None
    assert (
        client._normalize_privmsg(
            "@login=viewer;target-msg-id=abc :tmi.twitch.tv CLEARMSG #example :deleted text"
        )
        is None
    )
    assert client._normalize_privmsg(":viewer!viewer@viewer.tmi.twitch.tv JOIN #example") is None
