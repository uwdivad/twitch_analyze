"""EventSub WebSocket message handling: welcome/subscribe, reconnect and notifications."""

from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from app.ingestion.twitch import TWITCH_EVENTSUB_WS, TWITCH_HELIX, TwitchEventSubClient
from app.models.chat import ChannelInfo, ChatMessage

RECONNECT_URL = "wss://eventsub.wss.twitch.tv/ws?reconnect=abc"


class FakeResponse:
    def __init__(self, status: int = 202, body: str = "") -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def text(self) -> str:
        return self._body


class FakeWs:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.delivered = 0
        self.closed = False

    async def __aenter__(self) -> "FakeWs":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    def __aiter__(self) -> "FakeWs":
        return self

    async def __anext__(self) -> SimpleNamespace:
        if self.closed or not self._payloads:
            raise StopAsyncIteration
        payload = self._payloads.pop(0)
        self.delivered += 1
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, json=lambda: payload)

    async def close(self) -> None:
        self.closed = True


class FakeHttp:
    """Stands in for the aiohttp.ClientSession owned by run_forever()."""

    def __init__(self, sockets: list[FakeWs] | None = None, post_status: int = 202) -> None:
        self._sockets = list(sockets or [])
        self.post_status = post_status
        self.ws_urls: list[str] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def ws_connect(self, url: str, heartbeat: float) -> FakeWs:
        self.ws_urls.append(url)
        return self._sockets.pop(0)

    def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
        self.posts.append((url, json))
        return FakeResponse(self.post_status, "forbidden")


def make_client(http: FakeHttp) -> tuple[TwitchEventSubClient, list[ChatMessage], list[dict[str, Any]]]:
    messages: list[ChatMessage] = []
    statuses: list[dict[str, Any]] = []

    async def on_message(message: ChatMessage) -> None:
        messages.append(message)

    async def on_status(status: dict[str, Any]) -> None:
        statuses.append(status)

    client = TwitchEventSubClient(
        client_id="client",
        access_token="token",
        channels=["example", "other"],
        user_id="user-1",
        on_message=on_message,
        on_status=on_status,
    )
    client._http = http  # type: ignore[assignment]
    client._channels_by_id = {
        "channel-1": ChannelInfo(channel_id="channel-1", channel_login="example", channel_display_name="Example"),
        "channel-2": ChannelInfo(channel_id="channel-2", channel_login="other", channel_display_name="Other"),
    }
    return client, messages, statuses


def welcome(session_id: str = "session-1") -> dict[str, Any]:
    return {
        "metadata": {"message_type": "session_welcome"},
        "payload": {"session": {"id": session_id, "status": "connected"}},
    }


def reconnect(url: str | None = RECONNECT_URL) -> dict[str, Any]:
    return {
        "metadata": {"message_type": "session_reconnect"},
        "payload": {"session": {"id": "session-1", "status": "reconnecting", "reconnect_url": url}},
    }


def notification(subscription_type: str = "channel.chat.message") -> dict[str, Any]:
    return {
        "metadata": {
            "message_id": "eventsub-1",
            "message_type": "notification",
            "message_timestamp": "2026-04-28T10:15:30.123Z",
        },
        "payload": {
            "subscription": {"type": subscription_type},
            "event": {
                "broadcaster_user_id": "channel-1",
                "broadcaster_user_login": "example",
                "broadcaster_user_name": "Example",
                "chatter_user_id": "chatter-1",
                "chatter_user_login": "viewer",
                "chatter_user_name": "Viewer",
                "message_id": "message-1",
                "message": {"text": "hello", "fragments": [{"type": "text", "text": "hello"}]},
            },
        },
    }


@pytest.mark.anyio
async def test_session_welcome_subscribes_every_channel_to_the_session() -> None:
    http = FakeHttp()
    client, _messages, statuses = make_client(http)

    await client._handle_ws_payload(welcome("session-1"))

    assert statuses[0] == {"state": "connected", "session_id": "session-1"}
    assert [url for url, _body in http.posts] == [f"{TWITCH_HELIX}/eventsub/subscriptions"] * 2
    bodies = [body for _url, body in http.posts]
    assert [body["condition"] for body in bodies] == [
        {"broadcaster_user_id": "channel-1", "user_id": "user-1"},
        {"broadcaster_user_id": "channel-2", "user_id": "user-1"},
    ]
    assert all(body["type"] == "channel.chat.message" for body in bodies)
    assert all(body["transport"] == {"method": "websocket", "session_id": "session-1"} for body in bodies)
    assert [status["state"] for status in statuses] == ["connected", "subscribed", "subscribed"]


@pytest.mark.anyio
async def test_session_welcome_raises_when_subscription_is_rejected() -> None:
    http = FakeHttp(post_status=403)
    client, _messages, _statuses = make_client(http)

    with pytest.raises(RuntimeError, match="failed to subscribe to example: 403"):
        await client._handle_ws_payload(welcome())


@pytest.mark.anyio
async def test_session_reconnect_records_url_without_subscribing() -> None:
    http = FakeHttp()
    client, _messages, statuses = make_client(http)

    await client._handle_ws_payload(reconnect())

    assert client._reconnect_url == RECONNECT_URL
    assert statuses == [{"state": "reconnect_requested", "reconnect_url": RECONNECT_URL}]
    assert http.posts == []


@pytest.mark.anyio
async def test_session_reconnect_without_url_keeps_default() -> None:
    client, _messages, _statuses = make_client(FakeHttp())

    await client._handle_ws_payload(reconnect(url=None))

    assert client._reconnect_url is None


@pytest.mark.anyio
async def test_reconnect_switches_url_and_resumed_session_is_not_resubscribed() -> None:
    first = FakeWs([welcome("session-1"), reconnect(), notification()])
    resumed = FakeWs([welcome("session-2"), notification()])
    fresh = FakeWs([welcome("session-3")])
    http = FakeHttp([first, resumed, fresh])
    client, messages, statuses = make_client(http)

    # First connection: default URL, subscribes, then Twitch asks for a reconnect.
    await client._connect_once()
    assert http.ws_urls == [TWITCH_EVENTSUB_WS]
    assert first.closed is True
    assert first.delivered == 2  # the connection is dropped right after session_reconnect
    assert messages == []
    assert [body["transport"]["session_id"] for _url, body in http.posts] == ["session-1", "session-1"]

    # Second connection: dials the reconnect URL; the welcome must not re-subscribe.
    await client._connect_once()
    assert http.ws_urls == [TWITCH_EVENTSUB_WS, RECONNECT_URL]
    assert len(http.posts) == 2
    assert client._reconnect_url is None
    assert client._resuming_session is False
    assert [message.message_id for message in messages] == ["message-1"]
    connecting = [status for status in statuses if status["state"] == "connecting"]
    assert [status["resuming"] for status in connecting] == [False, True]

    # A later, non-resumed connection uses the default URL and subscribes again.
    await client._connect_once()
    assert http.ws_urls == [TWITCH_EVENTSUB_WS, RECONNECT_URL, TWITCH_EVENTSUB_WS]
    assert [body["transport"]["session_id"] for _url, body in http.posts[2:]] == ["session-3", "session-3"]


@pytest.mark.anyio
async def test_notification_dispatches_chat_message() -> None:
    http = FakeHttp()
    client, messages, statuses = make_client(http)

    await client._handle_ws_payload(notification())

    assert len(messages) == 1
    message = messages[0]
    assert message.message_id == "message-1"
    assert message.eventsub_message_id == "eventsub-1"
    assert message.channel_login == "example"
    assert message.message_text == "hello"
    assert message.session_id == "channel-1:2026-04-28"
    assert statuses == []
    assert http.posts == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        notification("channel.follow"),
        {"metadata": {"message_type": "session_keepalive"}, "payload": {}},
        {"metadata": {"message_type": "something_new"}, "payload": {}},
    ],
)
async def test_non_chat_payloads_do_not_dispatch_messages(payload: dict[str, Any]) -> None:
    http = FakeHttp()
    client, messages, statuses = make_client(http)

    await client._handle_ws_payload(payload)

    assert messages == []
    assert statuses == []
    assert http.posts == []


@pytest.mark.anyio
async def test_revocation_reports_status() -> None:
    client, messages, statuses = make_client(FakeHttp())

    await client._handle_ws_payload(
        {"metadata": {"message_type": "revocation"}, "payload": {"subscription": {"status": "authorization_revoked"}}}
    )

    assert messages == []
    assert statuses == [{"state": "revoked", "payload": {"subscription": {"status": "authorization_revoked"}}}]
