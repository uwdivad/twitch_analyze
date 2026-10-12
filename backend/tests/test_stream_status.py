"""Streamer live-status lookup: handle parsing, per-platform parsers, service and route."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import Settings, get_settings
from app.models.streams import PlatformStatus, StreamStatusResponse
from app.services.stream_status import (
    TWITCH_STREAM_QUERY,
    StreamStatusService,
    kick_status_from_json,
    normalize_handle,
    parse_youtube_live_page,
    twitch_status_from_gql,
    youtube_status_from_page,
)

CHECKED_AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# normalize_handle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "raw", "expected"),
    [
        ("twitch", "xQc", "xqc"),
        ("twitch", " @Pokimane ", "pokimane"),
        ("twitch", "https://www.twitch.tv/Sodapoppin", "sodapoppin"),
        ("twitch", "twitch.tv/ludwig/", "ludwig"),
        ("kick", "https://kick.com/XQC", "xqc"),
        ("kick", "Adin.Ross", "adin.ross"),
        ("youtube", "@LofiGirl", "LofiGirl"),
        ("youtube", "https://www.youtube.com/@LofiGirl/live", "LofiGirl"),
        ("youtube", "https://youtube.com/c/SomeName/videos", "SomeName"),
        ("youtube", "youtube.com/user/OldStyle", "OldStyle"),
        ("youtube", "Mr.Beast", "Mr.Beast"),
    ],
)
def test_normalize_handle_accepts_names_handles_and_urls(platform, raw, expected) -> None:
    assert normalize_handle(platform, raw) == expected


@pytest.mark.parametrize(
    ("platform", "raw"),
    [
        ("twitch", ""),
        ("twitch", "   "),
        ("twitch", "bad name"),
        ("twitch", "https://youtube.com/@someone"),
        ("kick", "https://kick.com/"),
        ("youtube", "https://twitch.tv/xqc"),
        ("youtube", "a" * 65),
        ("youtube", "name!"),
    ],
)
def test_normalize_handle_rejects_other_input(platform, raw) -> None:
    with pytest.raises(ValueError):
        normalize_handle(platform, raw)


# ---------------------------------------------------------------------------
# Twitch
# ---------------------------------------------------------------------------


def _twitch_payload(*, user=True, stream=None):
    if not user:
        return {"data": {"user": None}}
    return {
        "data": {
            "user": {
                "id": "487510275",
                "login": "lofigirl",
                "displayName": "LofiGirl",
                "profileImageURL": "https://static-cdn.jtvnw.net/lofi.png",
                "stream": stream,
            }
        }
    }


def test_twitch_status_live() -> None:
    stream = {
        "id": "317974995172",
        "title": "lofi hip hop radio",
        "viewersCount": 232,
        "createdAt": "2026-10-10T07:21:02Z",
        "type": "live",
        "game": {"name": "Music"},
    }

    status = twitch_status_from_gql(_twitch_payload(stream=stream), "lofigirl", CHECKED_AT)

    assert status.platform == "twitch"
    assert status.state == "live"
    assert status.display_name == "LofiGirl"
    assert status.channel_url == "https://twitch.tv/lofigirl"
    assert status.avatar_url.endswith("lofi.png")
    assert status.channel_id == "487510275"
    assert status.title == "lofi hip hop radio"
    assert status.category == "Music"
    assert status.viewer_count == 232
    assert status.started_at == datetime(2026, 10, 10, 7, 21, 2, tzinfo=UTC)
    assert status.checked_at == CHECKED_AT


def test_twitch_status_offline_and_not_found() -> None:
    offline = twitch_status_from_gql(_twitch_payload(stream=None), "lofigirl", CHECKED_AT)
    assert offline.state == "offline"
    assert offline.display_name == "LofiGirl"
    assert offline.viewer_count is None

    missing = twitch_status_from_gql(_twitch_payload(user=False), "nobody", CHECKED_AT)
    assert missing.state == "not_found"
    assert "nobody" in missing.detail
    assert missing.channel_url == "https://twitch.tv/nobody"


# ---------------------------------------------------------------------------
# Kick
# ---------------------------------------------------------------------------


def _kick_payload(livestream=None, **extra):
    payload = {
        "id": 668,
        "slug": "xqc",
        "is_banned": False,
        "livestream": livestream,
        "user": {"username": "xQc", "profile_pic": "https://files.kick.com/xqc.jpg"},
    }
    payload.update(extra)
    return payload


def test_kick_status_live() -> None:
    livestream = {
        "id": 1,
        "session_title": "GAMING",
        "created_at": "2026-10-11 10:00:00",
        "viewer_count": 51234,
        "is_live": True,
        "thumbnail": {"url": "https://images.kick.com/thumb.jpg"},
        "categories": [{"name": "Just Chatting"}],
    }

    status = kick_status_from_json(_kick_payload(livestream), "xqc", CHECKED_AT)

    assert status.state == "live"
    assert status.display_name == "xQc"
    assert status.channel_url == "https://kick.com/xqc"
    assert status.avatar_url.endswith("xqc.jpg")
    assert status.channel_id == "668"
    assert status.title == "GAMING"
    assert status.category == "Just Chatting"
    assert status.viewer_count == 51234
    assert status.started_at == datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
    assert status.thumbnail_url == "https://images.kick.com/thumb.jpg"


def test_kick_status_offline_and_banned() -> None:
    offline = kick_status_from_json(_kick_payload(None), "xqc", CHECKED_AT)
    assert offline.state == "offline"
    assert offline.display_name == "xQc"

    banned = kick_status_from_json(_kick_payload(None, is_banned=True), "xqc", CHECKED_AT)
    assert banned.state == "offline"
    assert banned.detail == "Channel is banned"


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

LIVE_PAGE = """<html><head>
<link rel="canonical" href="https://www.youtube.com/watch?v=1-LpQekNa9g">
<meta property="og:title" content="24/7 halloween ambience &#127875; spooky lofi">
<meta property="og:image" content="https://i.ytimg.com/vi/1-LpQekNa9g/maxresdefault.jpg">
</head><body><script>var ytInitialPlayerResponse = {"videoDetails":{"channelId":"UCSJ4gkVC6NrvII8umztf0Ow",
"isLiveContent":true},"microformat":{"playerMicroformatRenderer":{"ownerChannelName":"Lofi \\u0026 Girl",
"liveBroadcastDetails":{"isLiveNow":true,"startTimestamp":"2026-10-01T17:00:26+00:00"}}},
"originalViewCount":"3508"}</script></body></html>"""

SCHEDULED_PAGE = LIVE_PAGE.replace('"isLiveNow":true', '"isLiveNow":false')

OFFLINE_PAGE = """<html><head>
<link rel="canonical" href="https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ">
<meta property="og:title" content="Marques Brownlee">
<meta property="og:image" content="https://yt3.googleusercontent.com/channel.jpg">
</head><body><script>{"avatar":{"thumbnails":[{"url":"https://yt3.googleusercontent.com/avatar=s88"}]}}</script>
</body></html>"""


def test_parse_youtube_live_page_live() -> None:
    page = parse_youtube_live_page(LIVE_PAGE)

    assert page.canonical_url == "https://www.youtube.com/watch?v=1-LpQekNa9g"
    assert page.video_id == "1-LpQekNa9g"
    assert page.channel_id == "UCSJ4gkVC6NrvII8umztf0Ow"
    assert page.is_live_now is True
    assert page.title == "24/7 halloween ambience 🎃 spooky lofi"
    assert page.owner_name == "Lofi & Girl"
    assert page.image_url.endswith("maxresdefault.jpg")
    assert page.viewer_count == 3508
    assert page.started_at == datetime(2026, 10, 1, 17, 0, 26, tzinfo=UTC)


def test_youtube_status_live() -> None:
    status = youtube_status_from_page(parse_youtube_live_page(LIVE_PAGE), "LofiGirl", CHECKED_AT)

    assert status.state == "live"
    assert status.video_id == "1-LpQekNa9g"
    assert status.channel_id == "UCSJ4gkVC6NrvII8umztf0Ow"
    assert status.display_name == "Lofi & Girl"
    assert status.channel_url == "https://www.youtube.com/@LofiGirl"
    assert status.viewer_count == 3508
    assert status.thumbnail_url.endswith("maxresdefault.jpg")


def test_youtube_status_scheduled_stream_is_offline() -> None:
    status = youtube_status_from_page(parse_youtube_live_page(SCHEDULED_PAGE), "LofiGirl", CHECKED_AT)

    assert status.state == "offline"
    assert status.detail == "Scheduled stream"
    assert status.video_id == "1-LpQekNa9g"


def test_youtube_status_offline_channel_page() -> None:
    page = parse_youtube_live_page(OFFLINE_PAGE)
    assert page.video_id == ""
    assert page.is_live_now is None
    assert page.channel_id == "UCBJycsmduvYEL83R_U4JriQ"

    status = youtube_status_from_page(page, "mkbhd", CHECKED_AT)

    assert status.state == "offline"
    assert status.display_name == "Marques Brownlee"
    assert status.avatar_url == "https://yt3.googleusercontent.com/avatar=s88"
    assert status.channel_id == "UCBJycsmduvYEL83R_U4JriQ"


def test_youtube_status_unreadable_page_is_error() -> None:
    status = youtube_status_from_page(parse_youtube_live_page("<html>consent wall</html>"), "x", CHECKED_AT)

    assert status.state == "error"
    assert "YouTube" in status.detail


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FakeGql:
    """Stands in for TwitchGqlClient; records queries and answers from `payloads`."""

    calls: list[tuple[str, dict]] = []
    payload: dict = {}
    error: Exception | None = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def query(self, query, variables):
        FakeGql.calls.append((query, variables))
        if FakeGql.error is not None:
            raise FakeGql.error
        return FakeGql.payload


def _service(responses: dict[str, tuple[int, str]], gql=FakeGql) -> tuple[StreamStatusService, list[str]]:
    """Service whose HTTP fetches answer from `responses` keyed by URL substring."""
    urls: list[str] = []

    async def fetch_text(url, headers, cookies, timeout):
        urls.append(url)
        for needle, response in responses.items():
            if needle in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return 404, "not found"

    return StreamStatusService(gql_factory=gql, fetch_text=fetch_text), urls


@pytest.fixture(autouse=True)
def _reset_fake_gql():
    FakeGql.calls = []
    FakeGql.payload = _twitch_payload(stream={"title": "t", "viewersCount": 1, "createdAt": "2026-10-10T07:21:02Z"})
    FakeGql.error = None
    yield


@pytest.mark.anyio
async def test_lookup_checks_all_platforms_concurrently_and_in_order() -> None:
    service, urls = _service(
        {
            "kick.com/api/v2/channels/lofigirl": (200, '{"id": 1, "slug": "lofigirl", "livestream": null, "user": {"username": "LofiGirl"}}'),
            "/@LofiGirl/live": (200, LIVE_PAGE),
        }
    )

    response = await service.lookup("LofiGirl", {}, _settings())

    assert isinstance(response, StreamStatusResponse)
    assert response.name == "LofiGirl"
    assert [status.platform for status in response.platforms] == ["twitch", "kick", "youtube"]
    assert [status.state for status in response.platforms] == ["live", "offline", "live"]
    assert [status.handle for status in response.platforms] == ["lofigirl", "lofigirl", "LofiGirl"]
    assert FakeGql.calls == [(TWITCH_STREAM_QUERY, {"login": "lofigirl"})]
    assert any("kick.com/api/v2/channels/lofigirl" in url for url in urls)
    assert any(url.endswith("/@LofiGirl/live") for url in urls)


@pytest.mark.anyio
async def test_lookup_applies_per_platform_overrides() -> None:
    service, urls = _service({"/@OtherTube/live": (200, OFFLINE_PAGE), "channels/otherkick": (200, '{"slug": "otherkick"}')})

    response = await service.lookup("someone", {"youtube": "@OtherTube", "kick": "https://kick.com/OtherKick"}, _settings())

    assert [status.handle for status in response.platforms] == ["someone", "otherkick", "OtherTube"]
    assert response.platforms[2].state == "offline"
    assert FakeGql.calls[0][1] == {"login": "someone"}


@pytest.mark.anyio
async def test_lookup_reports_one_failing_platform_without_failing_the_request() -> None:
    FakeGql.error = RuntimeError("Twitch GQL request failed: 503")
    service, _urls = _service(
        {
            "kick.com": (403, "<html>Just a moment...</html>"),
            "/@LofiGirl/live": (200, LIVE_PAGE),
        }
    )

    response = await service.lookup("LofiGirl", {}, _settings())

    twitch, kick, youtube = response.platforms
    assert twitch.state == "error"
    assert "503" in twitch.detail
    assert kick.state == "error"
    assert "403" in kick.detail and "Cloudflare" in kick.detail
    assert youtube.state == "live"


@pytest.mark.anyio
async def test_lookup_not_found_handles() -> None:
    FakeGql.payload = _twitch_payload(user=False)
    service, urls = _service({"kick.com": (404, "{}")})

    response = await service.lookup("nobody_here", {}, _settings())

    assert [status.state for status in response.platforms] == ["not_found", "not_found", "not_found"]
    # YouTube falls back to the legacy /c/<name>/live URL before giving up.
    assert [url for url in urls if "youtube" in url] == [
        "https://www.youtube.com/@nobody_here/live",
        "https://www.youtube.com/c/nobody_here/live",
    ]


@pytest.mark.anyio
async def test_lookup_invalid_handle_for_one_platform_is_that_platforms_error() -> None:
    # A dot is fine for Kick/YouTube but not a valid Twitch login.
    service, _urls = _service({"kick.com": (200, '{"slug": "adin.ross"}'), "/live": (200, OFFLINE_PAGE)})

    response = await service.lookup("adin.ross", {"twitch": "bad name"}, _settings())

    assert response.platforms[0].state == "error"
    assert "invalid twitch handle" in response.platforms[0].detail
    assert response.platforms[1].state == "offline"


@pytest.mark.anyio
async def test_lookup_rejects_unusable_names() -> None:
    service, _urls = _service({})
    with pytest.raises(ValueError):
        await service.lookup("   ", {}, _settings())
    with pytest.raises(ValueError):
        await service.lookup("bad name!", {}, _settings())


@pytest.mark.anyio
async def test_status_is_cached_per_handle_until_ttl_expires() -> None:
    service, urls = _service({"kick.com": (200, '{"slug": "xqc", "livestream": null}')})
    clock = [1000.0]
    service._now = lambda: clock[0]
    settings = _settings(stream_status_cache_seconds=30)

    first = await service.status("kick", "xqc", settings)
    clock[0] += 10
    second = await service.status("kick", "xqc", settings)
    assert second is first
    assert len(urls) == 1

    clock[0] += 25  # past the 30 s TTL
    third = await service.status("kick", "xqc", settings)
    assert third is not first
    assert len(urls) == 2

    # Other handles are not served from this entry.
    await service.status("kick", "other", settings)
    assert len(urls) == 3


@pytest.mark.anyio
async def test_status_cache_disabled_with_zero_ttl() -> None:
    service, urls = _service({"kick.com": (200, '{"slug": "xqc"}')})
    settings = _settings(stream_status_cache_seconds=0)

    await service.status("kick", "xqc", settings)
    await service.status("kick", "xqc", settings)

    assert len(urls) == 2


@pytest.mark.anyio
async def test_status_timeout_becomes_error(monkeypatch) -> None:
    async def slow_fetch(url, headers, cookies, timeout):
        await asyncio.sleep(10)
        return 200, "{}"

    service = StreamStatusService(gql_factory=FakeGql, fetch_text=slow_fetch)

    async def instant_timeout(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr("app.services.stream_status.asyncio.wait_for", instant_timeout)

    status = await service.status("kick", "xqc", _settings(stream_status_timeout_seconds=1))

    assert status.state == "error"
    assert "did not answer" in status.detail


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


class FakeStatusService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.error: Exception | None = None

    async def lookup(self, name, overrides, settings):
        self.calls.append((name, overrides))
        if self.error is not None:
            raise self.error
        return StreamStatusResponse(
            name=name,
            checked_at=CHECKED_AT,
            platforms=[
                PlatformStatus(platform=platform, handle=name, state="offline", checked_at=CHECKED_AT)
                for platform in ("twitch", "kick", "youtube")
            ],
        )


@pytest.mark.anyio
async def test_stream_status_route_passes_trimmed_overrides() -> None:
    service = FakeStatusService()

    response = await routes.stream_status(
        name="LofiGirl", twitch=None, kick="  ", youtube=" @Other ", settings=_settings(), service=service
    )

    assert service.calls == [("LofiGirl", {"youtube": "@Other"})]
    assert len(response.platforms) == 3


@pytest.mark.anyio
async def test_stream_status_route_maps_value_error_to_400() -> None:
    service = FakeStatusService()
    service.error = ValueError("invalid streamer name: 'bad name!'")

    with pytest.raises(HTTPException) as excinfo:
        await routes.stream_status(name="bad name!", twitch=None, kick=None, youtube=None, settings=_settings(), service=service)

    assert excinfo.value.status_code == 400
    assert "bad name!" in excinfo.value.detail


@pytest.fixture
def status_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def factory(**overrides):
        settings = Settings(_env_file=None, settings_overrides_file=str(tmp_path / "overrides.json"), **overrides)
        monkeypatch.setattr(routes, "get_settings", lambda: settings)
        app = FastAPI()
        app.include_router(routes.router)
        app.state.stream_status = FakeStatusService()
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    return factory


def test_stream_status_route_is_wired_through_app_state(status_client) -> None:
    client = status_client()

    response = client.get("/api/streams/status", params={"name": "LofiGirl", "kick": "other"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "LofiGirl"
    assert [item["platform"] for item in body["platforms"]] == ["twitch", "kick", "youtube"]
    assert client.app.state.stream_status.calls == [("LofiGirl", {"kick": "other"})]


def test_stream_status_route_requires_feature_flag(status_client) -> None:
    client = status_client(enable_stream_status=False)

    response = client.get("/api/streams/status", params={"name": "LofiGirl"})

    assert response.status_code == 403
    assert "Streamer live status" in response.json()["detail"]
    assert client.app.state.stream_status.calls == []


def test_stream_status_route_requires_name(status_client) -> None:
    client = status_client()

    assert client.get("/api/streams/status").status_code == 422
    assert client.get("/api/streams/status", params={"name": ""}).status_code == 422
