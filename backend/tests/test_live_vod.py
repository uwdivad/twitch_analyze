from datetime import UTC, datetime

import pytest

from app.ingestion.live_vod import (
    CHANNEL_LIVE_VOD_QUERY,
    ChannelNotFoundError,
    ChannelNotLiveError,
    NoLiveVodError,
    TwitchLiveVodSource,
    live_vod_from_gql,
    parse_channel_reference,
)


def _payload(*, stream=None, videos=None, user=True):
    if not user:
        return {"data": {"user": None}}
    return {
        "data": {
            "user": {
                "id": "71092938",
                "login": "xqc",
                "displayName": "xQc",
                "stream": stream,
                "videos": {"edges": [{"node": node} for node in (videos or [])]},
            }
        }
    }


def _video(video_id: str, created_at: str, length: int = 120):
    return {"id": video_id, "title": f"VOD {video_id}", "lengthSeconds": length, "createdAt": created_at}


def test_parse_channel_reference_accepts_logins_and_urls() -> None:
    assert parse_channel_reference("xQc") == "xqc"
    assert parse_channel_reference("  @Pokimane ") == "pokimane"
    assert parse_channel_reference("https://www.twitch.tv/Sodapoppin") == "sodapoppin"
    assert parse_channel_reference("twitch.tv/ludwig/") == "ludwig"
    assert parse_channel_reference("https://m.twitch.tv/ludwig") == "ludwig"


@pytest.mark.parametrize(
    "value",
    ["", "   ", "https://youtube.com/@someone", "https://www.twitch.tv/videos/123", "twitch.tv/", "bad name", "a" * 26],
)
def test_parse_channel_reference_rejects_other_input(value: str) -> None:
    with pytest.raises(ValueError):
        parse_channel_reference(value)


def test_live_vod_from_gql_picks_archive_matching_the_stream() -> None:
    payload = _payload(
        stream={"id": "1", "createdAt": "2026-10-08T18:00:05Z"},
        videos=[
            _video("300", "2026-10-08T18:00:00Z", 1500),
            _video("200", "2026-10-07T18:00:00Z"),
        ],
    )

    vod = live_vod_from_gql(payload, "xqc")

    assert vod.platform == "twitch"
    assert vod.video_id == "300"
    assert vod.title == "VOD 300"
    assert vod.channel_id == "71092938"
    assert vod.channel_login == "xqc"
    assert vod.channel_display_name == "xQc"
    assert vod.created_at == datetime(2026, 10, 8, 18, 0, 0, tzinfo=UTC)
    assert vod.duration_seconds == 1500


def test_live_vod_from_gql_errors() -> None:
    with pytest.raises(ChannelNotFoundError):
        live_vod_from_gql(_payload(user=False), "nobody")
    with pytest.raises(ChannelNotLiveError):
        live_vod_from_gql(_payload(stream=None, videos=[_video("1", "2026-10-08T18:00:00Z")]), "xqc")
    # Newest archive is from yesterday's stream: not the current one.
    with pytest.raises(NoLiveVodError):
        live_vod_from_gql(
            _payload(stream={"id": "1", "createdAt": "2026-10-08T18:00:00Z"}, videos=[_video("1", "2026-10-07T18:00:00Z")]),
            "xqc",
        )
    with pytest.raises(NoLiveVodError):
        live_vod_from_gql(_payload(stream={"id": "1", "createdAt": "2026-10-08T18:00:00Z"}, videos=[]), "xqc")


@pytest.mark.anyio
async def test_twitch_source_queries_by_login() -> None:
    calls = []

    class FakeGql:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def query(self, query, variables):
            calls.append((query, variables))
            return _payload(stream={"id": "1", "createdAt": "2026-10-08T18:00:00Z"}, videos=[_video("9", "2026-10-08T18:00:01Z")])

    vod = await TwitchLiveVodSource(FakeGql).resolve("xqc")

    assert vod.video_id == "9"
    assert calls == [(CHANNEL_LIVE_VOD_QUERY, {"login": "xqc"})]
