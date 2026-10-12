"""Fail-soft and error-mapping behavior of API route handlers, called directly."""

import math
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import routes
from app.core import runtime_settings
from app.core.config import Settings
from app.models.chat import ChatMessage, GenerateSummaryRequest, StartTranscriptionRequest
from app.models.settings import SettingsUpdate
from app.models.vod import VodAnalysis


class RaisingClickHouse:
    """Every ClickHouse call fails, like an unreachable server."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        async def fail(*_args, **_kwargs):
            self.calls.append(name)
            raise RuntimeError("clickhouse down: secret connection detail")

        return fail


# (handler, kwargs, expected fallback, ClickHouse method the handler must have called)
FAIL_SOFT_CASES = [
    pytest.param(routes.volume, {"channel": "example", "session_id": None, "limit": 60}, [], "volume_by_minute", id="volume"),
    pytest.param(routes.volume_by_channel, {"limit": 60}, {}, "volume_by_minute_for_channels", id="volume_by_channel"),
    pytest.param(
        routes.top_chatters, {"channel": "example", "session_id": None, "limit": 10}, [], "top_chatters", id="top_chatters"
    ),
    pytest.param(
        routes.top_emotes, {"channel": "example", "session_id": None, "limit": 10}, [], "top_emotes", id="top_emotes"
    ),
    pytest.param(routes.summaries, {"channel": "example", "limit": 10}, [], "recent_summaries", id="summaries"),
    pytest.param(routes.vod_analyses, {"limit": 10}, [], "recent_vod_analyses", id="vod_analyses"),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("handler", "kwargs", "expected", "method"), FAIL_SOFT_CASES)
async def test_analytics_handlers_fail_soft_when_clickhouse_raises(handler, kwargs, expected, method) -> None:
    clickhouse = RaisingClickHouse()

    result = await handler(clickhouse=clickhouse, **kwargs)

    assert result == expected
    assert clickhouse.calls == [method]


@pytest.mark.anyio
async def test_message_total_fails_soft_to_zero() -> None:
    clickhouse = RaisingClickHouse()

    result = await routes.message_total(channel="example", session_id=None, clickhouse=clickhouse)

    assert result.count == 0
    assert clickhouse.calls == ["message_total"]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class RaisingSummaryService:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def generate(self, **_kwargs):
        raise self.exc


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (ValueError("No chat messages found"), 404),
        (KeyError("unexpected"), 500),
        (Exception("boom: secret detail"), 500),
    ],
)
async def test_generate_summary_maps_errors_to_status(exc: Exception, status: int) -> None:
    with pytest.raises(HTTPException) as caught:
        await routes.generate_summary(
            request=GenerateSummaryRequest(channel="example", window_minutes=60),
            service=RaisingSummaryService(exc),
        )

    assert caught.value.status_code == status
    if status == 500:
        assert "secret" not in str(caught.value.detail)
        assert caught.value.detail == "Failed to generate chat summary"


def _message() -> ChatMessage:
    return ChatMessage(
        message_id="message-1",
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        session_id="channel-1:2026-04-28",
        session_date=date(2026, 4, 28),
        chatter_user_id="user-1",
        chatter_login="viewer",
        chatter_display_name="Viewer",
        message_text="hello",
        event_ts=datetime(2026, 4, 28, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_insert_messages_returns_500_when_clickhouse_raises() -> None:
    clickhouse = RaisingClickHouse()

    with pytest.raises(HTTPException) as caught:
        await routes.insert_messages(messages=[_message()], clickhouse=clickhouse)

    assert caught.value.status_code == 500
    assert "secret" not in str(caught.value.detail)
    assert clickhouse.calls == ["insert_messages"]


@pytest.mark.anyio
async def test_update_settings_returns_500_when_overrides_cannot_be_written(monkeypatch) -> None:
    def disk_full(_values):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(runtime_settings, "save_overrides", disk_full)
    ingestion_restarts: list[object] = []

    class Ingestion:
        async def restart(self, settings) -> None:
            ingestion_restarts.append(settings)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ingestion=Ingestion())))

    with pytest.raises(HTTPException) as caught:
        await routes.update_settings(request, SettingsUpdate(values={"twitch_channels": "a,b"}))

    assert caught.value.status_code == 500
    assert caught.value.detail == "Failed to save settings"
    assert ingestion_restarts == []


@pytest.mark.anyio
async def test_start_transcription_returns_503_without_openai_key(monkeypatch) -> None:
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(_env_file=None, openai_api_key=""))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(transcription_jobs={}, transcription_tasks={}))
    )

    with pytest.raises(HTTPException) as caught:
        await routes.start_transcription(
            request=request,
            payload=StartTranscriptionRequest(channel="example", duration_minutes=1),
        )

    assert caught.value.status_code == 503
    assert request.app.state.transcription_jobs == {}
    assert request.app.state.transcription_tasks == {}


# ---------------------------------------------------------------------------
# VOD activity bucket clamp
# ---------------------------------------------------------------------------


class ActivityClickHouse:
    def __init__(self, analysis: VodAnalysis) -> None:
        self.analysis = analysis
        self.activity_kwargs: dict | None = None

    async def get_vod_analysis(self, video_id: str) -> VodAnalysis:
        return self.analysis

    async def vod_activity(self, **kwargs):
        self.activity_kwargs = kwargs
        return []


def _analysis(duration_seconds: int) -> VodAnalysis:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    return VodAnalysis(
        video_id="123456",
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        title="Marathon",
        video_created_at=now,
        duration_seconds=duration_seconds,
        bucket_seconds=60,
        message_count=100,
        unique_chatter_count=10,
        status="completed",
        analyzed_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("duration", "requested", "expected"),
    [
        # 36001 s / 3600 buckets -> ceil = 11 s; a 1 s request is raised to that.
        (routes.MAX_VOD_ACTIVITY_BUCKETS * 10 + 1, 1, 11),
        # Exactly at the limit: 10 s buckets give exactly MAX buckets, no extra raise.
        (routes.MAX_VOD_ACTIVITY_BUCKETS * 10, 1, 10),
        # A request already coarser than the clamp is kept.
        (routes.MAX_VOD_ACTIVITY_BUCKETS * 10 + 1, 30, 30),
    ],
)
async def test_vod_activity_raises_small_bucket_to_stay_under_max(duration: int, requested: int, expected: int) -> None:
    clickhouse = ActivityClickHouse(_analysis(duration))

    result = await routes.vod_activity(video_id="123456", bucket_seconds=requested, clickhouse=clickhouse)

    assert expected == max(requested, math.ceil(duration / routes.MAX_VOD_ACTIVITY_BUCKETS))
    assert result.bucket_seconds == expected
    assert clickhouse.activity_kwargs["bucket_seconds"] == expected
    assert clickhouse.activity_kwargs["duration_seconds"] == duration
    assert math.ceil(duration / result.bucket_seconds) <= routes.MAX_VOD_ACTIVITY_BUCKETS
