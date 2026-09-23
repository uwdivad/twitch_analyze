import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import pytest

from app.ingestion import vod_replay
from app.ingestion.vod_replay import (
    GqlHttpError,
    TwitchGqlClient,
    normalize_comment,
    parse_vod_reference,
    video_from_gql,
)
from app.models.vod import VodMetadata

HASH = "abc123"


def make_video() -> VodMetadata:
    return VodMetadata(
        video_id="987654321",
        title="Big stream",
        duration_seconds=3600,
        created_at=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        channel_id="42",
        channel_login="streamer",
        channel_display_name="Streamer",
    )


def make_node(node_id: str = "c1", offset: Any = 42, commenter: Any = "default") -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": node_id,
        "commenter": {"id": "123", "login": "user", "displayName": "User"} if commenter == "default" else commenter,
        "contentOffsetSeconds": offset,
        "createdAt": "2024-01-01T00:00:42Z",
        "message": {
            "fragments": [
                {"text": "hi ", "emote": None},
                {"text": "Kappa", "emote": {"id": "25;3", "emoteID": "25", "from": 3, "to": 8}},
            ],
            "userBadges": [{"id": "subscriber/12", "setID": "subscriber", "version": "12"}],
            "userColor": "#FF0000",
        },
    }
    if node_id is None:
        del node["id"]
    if offset is None:
        del node["contentOffsetSeconds"]
    return node


def make_client(fetch_json: Any, **kwargs: Any) -> TwitchGqlClient:
    return TwitchGqlClient(
        url="https://gql.example/gql",
        client_id="cid",
        comments_query_hash=HASH,
        page_delay_seconds=kwargs.pop("page_delay_seconds", 0),
        fetch_json=fetch_json,
        **kwargs,
    )


def comments_response(
    node_ids: list[str], has_next: bool, offsets: list[Any] | None = None
) -> list[dict[str, Any]]:
    # Default: offsets increase with the id's order in the alphabet, so paging makes progress.
    if offsets is None:
        offsets = [ord(node_id[0]) for node_id in node_ids]
    edges = [
        {"cursor": f"cur-{node_id}", "node": make_node(node_id, offset=offset)}
        for node_id, offset in zip(node_ids, offsets, strict=True)
    ]
    return [
        {
            "data": {
                "video": {
                    "id": "987654321",
                    "comments": {"edges": edges, "pageInfo": {"hasNextPage": has_next}},
                }
            }
        }
    ]


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(vod_replay, "_sleep", fake_sleep)
    return delays


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123456789", "123456789"),
        ("  123456789  ", "123456789"),
        ("v123456789", "123456789"),
        ("https://www.twitch.tv/videos/123456789", "123456789"),
        ("https://twitch.tv/videos/123456789", "123456789"),
        ("https://m.twitch.tv/videos/123456789", "123456789"),
        ("www.twitch.tv/videos/123456789", "123456789"),
        ("twitch.tv/videos/123456789", "123456789"),
        ("http://WWW.Twitch.TV/videos/123456789/", "123456789"),
        ("https://www.twitch.tv/videos/123456789?t=1h2m3s", "123456789"),
        ("https://www.twitch.tv/videos/123456789?filter=archives&sort=time", "123456789"),
        ("https://www.twitch.tv/somechannel/v/123456789", "123456789"),
        ("https://www.twitch.tv/somechannel/video/123456789", "123456789"),
    ],
)
def test_parse_vod_reference_accepts(value: str, expected: str) -> None:
    assert parse_vod_reference(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "https://youtube.com/videos/123",
        "https://nottwitch.tv/videos/123",
        "https://twitch.tv.evil.com/videos/123",
        "https://www.twitch.tv/videos/",
        "https://www.twitch.tv/somechannel",
        "https://www.twitch.tv/somechannel/clip/abc",
        "abc",
    ],
)
def test_parse_vod_reference_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        parse_vod_reference(value)


def test_video_from_gql() -> None:
    video = video_from_gql(
        {
            "data": {
                "video": {
                    "id": "987",
                    "title": "Title",
                    "lengthSeconds": 7200,
                    "createdAt": "2024-01-01T00:00:00Z",
                    "owner": {"id": "42", "login": "streamer", "displayName": "Streamer"},
                }
            }
        }
    )
    assert video.video_id == "987"
    assert video.duration_seconds == 7200
    assert video.created_at == datetime(2024, 1, 1, tzinfo=UTC)
    assert (video.channel_id, video.channel_login, video.channel_display_name) == ("42", "streamer", "Streamer")


@pytest.mark.parametrize("payload", [{"data": {"video": None}}, {"data": {}}, {}])
def test_video_from_gql_null_video(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="VOD not found"):
        video_from_gql(payload)


def test_normalize_comment_shapes() -> None:
    video = make_video()
    message = normalize_comment(make_node(), video)
    assert message is not None
    assert message.message_id == "c1"
    assert message.session_id == "vod:987654321"
    assert message.session_date == video.created_at.date()
    assert message.event_ts == video.created_at + timedelta(seconds=42)
    assert message.source == "vod"
    assert message.channel_id == "42"
    assert message.channel_login == "streamer"
    assert (message.chatter_user_id, message.chatter_login, message.chatter_display_name) == ("123", "user", "User")
    assert message.message_text == "hi Kappa"
    assert message.message_fragments == [
        {"type": "text", "text": "hi "},
        {"type": "emote", "text": "Kappa", "emote": {"id": "25"}},
    ]
    assert message.emotes == [
        {"type": "emote", "text": "Kappa", "emote": {"id": "25"}, "position": {"start": 3, "end": 7}}
    ]
    assert message.message_text[3 : 7 + 1] == "Kappa"
    assert message.badges == [{"set_id": "subscriber", "id": "12"}]
    assert message.raw_event["source"] == "twitch_vod"
    assert message.raw_event["video_id"] == "987654321"
    assert message.raw_event["content_offset_seconds"] == 42
    assert message.raw_event["node"]["id"] == "c1"


def test_normalize_comment_null_commenter() -> None:
    message = normalize_comment(make_node(commenter=None), make_video())
    assert message is not None
    assert (message.chatter_user_id, message.chatter_login, message.chatter_display_name) == ("", "", "[deleted]")


def test_normalize_comment_missing_fields() -> None:
    assert normalize_comment(make_node(node_id=None), make_video()) is None
    assert normalize_comment(make_node(offset=None), make_video()) is None


@pytest.mark.anyio
async def test_iter_comment_pages_paginates_and_dedups(no_sleep: list[float]) -> None:
    calls: list[Any] = []
    responses = [
        comments_response(["a", "b"], has_next=True),
        comments_response(["b", "c"], has_next=True),
        comments_response(["d"], has_next=False),
        comments_response(["never"], has_next=False),
    ]

    async def fetch_json(body: Any) -> Any:
        calls.append(body)
        return responses[len(calls) - 1]

    async with make_client(fetch_json, page_delay_seconds=0.25) as client:
        pages = [page async for page in client.iter_comment_pages("987654321")]

    assert [[node["id"] for node in page] for page in pages] == [["a", "b"], ["c"], ["d"]]
    assert len(calls) == 3
    first = calls[0]
    assert isinstance(first, list) and len(first) == 1
    assert first[0]["operationName"] == "VideoCommentsByOffsetOrCursor"
    assert first[0]["variables"] == {"videoID": "987654321", "contentOffsetSeconds": 0}
    assert first[0]["extensions"] == {"persistedQuery": {"version": 1, "sha256Hash": HASH}}
    assert calls[1][0]["variables"] == {"videoID": "987654321", "cursor": "cur-b"}
    assert calls[2][0]["variables"] == {"videoID": "987654321", "cursor": "cur-c"}
    assert no_sleep == [0.25, 0.25]


@pytest.mark.anyio
async def test_iter_comment_pages_stops_after_two_pages_without_new(no_sleep: list[float]) -> None:
    calls: list[Any] = []
    responses = [
        comments_response(["a", "b"], has_next=True),
        comments_response(["a", "b"], has_next=True),
        comments_response(["a", "b"], has_next=True),
        comments_response(["z"], has_next=False),
    ]

    async def fetch_json(body: Any) -> Any:
        calls.append(body)
        return responses[len(calls) - 1]

    async with make_client(fetch_json) as client:
        pages = [page async for page in client.iter_comment_pages("987654321")]

    assert [[node["id"] for node in page] for page in pages] == [["a", "b"]]
    assert len(calls) == 3


@pytest.mark.anyio
async def test_iter_comment_pages_handles_dict_and_null_video(no_sleep: list[float]) -> None:
    async def dict_response(body: Any) -> Any:
        return comments_response(["a"], has_next=False)[0]

    async with make_client(dict_response) as client:
        pages = [page async for page in client.iter_comment_pages("1")]
    assert [[node["id"] for node in page] for page in pages] == [["a"]]

    for payload in ([{"data": {"video": None}}], [{"data": {"video": {"id": "1", "comments": None}}}]):

        async def null_response(body: Any, payload: Any = payload) -> Any:
            return payload

        async with make_client(null_response) as client:
            assert [page async for page in client.iter_comment_pages("1")] == []


@pytest.mark.anyio
async def test_post_retries_429_then_succeeds(no_sleep: list[float]) -> None:
    attempts = 0

    async def fetch_json(body: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise GqlHttpError(429 if attempts == 1 else 503, "slow down")
        return comments_response(["a"], has_next=False)

    async with make_client(fetch_json) as client:
        pages = [page async for page in client.iter_comment_pages("1")]

    assert attempts == 3
    assert [[node["id"] for node in page] for page in pages] == [["a"]]
    assert no_sleep == [1, 2]


@pytest.mark.anyio
async def test_post_gives_up_after_max_retries(no_sleep: list[float]) -> None:
    attempts = 0

    async def fetch_json(body: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise GqlHttpError(500, "boom")

    async with make_client(fetch_json, max_retries=2) as client:
        with pytest.raises(RuntimeError, match="Twitch GQL request failed: 500 boom"):
            await client.fetch_video("1")
    assert attempts == 3
    assert no_sleep == [1, 2]


@pytest.mark.anyio
async def test_post_does_not_retry_client_errors(no_sleep: list[float]) -> None:
    attempts = 0

    async def fetch_json(body: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise GqlHttpError(400, "bad request")

    async with make_client(fetch_json) as client:
        with pytest.raises(RuntimeError, match="400 bad request"):
            await client.fetch_video("1")
    assert attempts == 1
    assert no_sleep == []


@pytest.mark.anyio
async def test_persisted_query_not_found_is_not_retried(no_sleep: list[float]) -> None:
    attempts = 0

    async def fetch_json(body: Any) -> Any:
        nonlocal attempts
        attempts += 1
        return [{"errors": [{"message": "PersistedQueryNotFound"}]}]

    async with make_client(fetch_json) as client:
        with pytest.raises(RuntimeError, match="TWITCH_GQL_COMMENTS_QUERY_HASH"):
            async for _ in client.iter_comment_pages("1"):
                pass
    assert attempts == 1
    assert no_sleep == []


@pytest.mark.anyio
async def test_other_gql_errors_raise(no_sleep: list[float]) -> None:
    async def fetch_json(body: Any) -> Any:
        return {"errors": [{"message": "service timeout"}, {"message": "other"}]}

    async with make_client(fetch_json) as client:
        with pytest.raises(RuntimeError, match="service timeout; other"):
            await client.fetch_video("1")


@pytest.mark.anyio
async def test_fetch_video_posts_raw_query_and_null_video_raises() -> None:
    calls: list[Any] = []

    async def fetch_json(body: Any) -> Any:
        calls.append(body)
        if len(calls) == 1:
            return {
                "data": {
                    "video": {
                        "id": "987",
                        "title": "T",
                        "lengthSeconds": 10,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "owner": {"id": "42", "login": "s", "displayName": "S"},
                    }
                }
            }
        return {"data": {"video": None}}

    async with make_client(fetch_json) as client:
        video = await client.fetch_video("987")
        assert video.video_id == "987"
        with pytest.raises(ValueError, match="VOD not found"):
            await client.fetch_video("404")

    assert calls[0]["variables"] == {"id": "987"}
    assert "video(id: $id)" in calls[0]["query"]
    assert "lengthSeconds" in calls[0]["query"]


def test_normalize_comment_non_finite_offset() -> None:
    assert normalize_comment(make_node(offset=float("nan")), make_video()) is None
    assert normalize_comment(make_node(offset=float("inf")), make_video()) is None


def test_normalize_comment_emote_position_after_emoji_and_id_fallback() -> None:
    node = make_node()
    node["message"]["fragments"] = [
        {"text": "\U0001F602 wow ", "emote": None},
        {"text": "Kappa", "emote": {"id": "25;7"}},
        {"text": " lol", "emote": None},
    ]
    message = normalize_comment(node, make_video())
    assert message is not None
    assert len(message.emotes) == 1
    emote = message.emotes[0]
    start, end = emote["position"]["start"], emote["position"]["end"]
    assert message.message_text[start : end + 1] == "Kappa"
    assert emote["emote"] == {"id": "25"}
    assert message.message_fragments[1] == {"type": "emote", "text": "Kappa", "emote": {"id": "25"}}


@pytest.mark.anyio
async def test_iter_comment_pages_stops_when_offset_stalls(no_sleep: list[float]) -> None:
    calls: list[Any] = []
    responses = [
        comments_response(["a1"], has_next=True, offsets=[10]),
        comments_response(["a2"], has_next=True, offsets=[10]),
        comments_response(["a3"], has_next=True, offsets=[9]),
        comments_response(["a4"], has_next=True, offsets=[10]),
        comments_response(["never"], has_next=False, offsets=[11]),
    ]

    async def fetch_json(body: Any) -> Any:
        calls.append(body)
        return responses[len(calls) - 1]

    async with make_client(fetch_json) as client:
        pages = [page async for page in client.iter_comment_pages("1")]

    # New ids keep arriving, but offsets never pass 10: stop after 3 stalled pages.
    assert [[node["id"] for node in page] for page in pages] == [["a1"], ["a2"], ["a3"], ["a4"]]
    assert len(calls) == 4


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def text(self) -> str:
        return self._body

    async def json(self, content_type: Any = None) -> Any:
        return json.loads(self._body)


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = responses
        self.posts: list[Any] = []

    def post(self, url: str, json: Any) -> _FakeResponse:
        self.posts.append(json)
        return self.responses[len(self.posts) - 1]

    async def close(self) -> None:
        return None


@pytest.mark.anyio
async def test_invalid_json_on_200_is_retried(no_sleep: list[float]) -> None:
    video_body = {
        "data": {
            "video": {
                "id": "987",
                "title": "T",
                "lengthSeconds": 10,
                "createdAt": "2024-01-01T00:00:00Z",
                "owner": {"id": "42", "login": "s", "displayName": "S"},
            }
        }
    }
    session = _FakeSession([_FakeResponse(200, "<html>oops</html>"), _FakeResponse(200, json.dumps(video_body))])
    client = TwitchGqlClient(url="https://gql.example/gql", client_id="cid", comments_query_hash=HASH)
    client._session = session  # type: ignore[assignment]

    video = await client.fetch_video("987")

    assert video.video_id == "987"
    assert len(session.posts) == 2
    assert no_sleep == [1]


@pytest.mark.anyio
async def test_invalid_json_exhausts_retries_as_runtime_error(no_sleep: list[float]) -> None:
    session = _FakeSession([_FakeResponse(200, "not json") for _ in range(3)])
    client = TwitchGqlClient(url="u", client_id="cid", comments_query_hash=HASH, max_retries=2)
    client._session = session  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="Twitch GQL request failed: 502 invalid JSON"):
        await client.fetch_video("987")
    assert len(session.posts) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("message", ["service timeout", "Service Error", "service unavailable"])
async def test_transient_gql_error_is_retried(no_sleep: list[float], message: str) -> None:
    attempts = 0

    async def fetch_json(body: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return [{"errors": [{"message": message}]}]
        return comments_response(["a"], has_next=False)

    async with make_client(fetch_json) as client:
        pages = [page async for page in client.iter_comment_pages("1")]

    assert attempts == 2
    assert [[node["id"] for node in page] for page in pages] == [["a"]]
    assert no_sleep == [1]


@pytest.mark.anyio
async def test_transient_gql_error_exhausts_retries(no_sleep: list[float]) -> None:
    async def fetch_json(body: Any) -> Any:
        return {"errors": [{"message": "service timeout"}]}

    async with make_client(fetch_json, max_retries=1) as client:
        with pytest.raises(RuntimeError, match="Twitch GQL request failed: gql service timeout"):
            await client.fetch_video("1")
    assert no_sleep == [1]


@pytest.mark.anyio
async def test_integrity_check_error_is_fatal(no_sleep: list[float]) -> None:
    attempts = 0

    async def fetch_json(body: Any) -> Any:
        nonlocal attempts
        attempts += 1
        return [{"errors": [{"message": "failed integrity check"}]}]

    async with make_client(fetch_json) as client:
        with pytest.raises(RuntimeError, match=r"integrity check\); the web Client-Id/hash may need updating"):
            await client.fetch_video("1")
    assert attempts == 1
    assert no_sleep == []


@pytest.mark.anyio
@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError("reset"), asyncio.TimeoutError()])
async def test_transport_errors_are_retried(no_sleep: list[float], error: Exception) -> None:
    attempts = 0

    async def fetch_json(body: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error
        return comments_response(["a"], has_next=False)

    async with make_client(fetch_json) as client:
        pages = [page async for page in client.iter_comment_pages("1")]

    assert attempts == 2
    assert [[node["id"] for node in page] for page in pages] == [["a"]]
    assert no_sleep == [1]