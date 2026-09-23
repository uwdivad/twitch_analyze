from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import (
    MAX_MESSAGE_BATCH_SIZE,
    _prune_finished_jobs,
    generate_summary,
    insert_messages,
    insert_summary,
    message_total,
    recent_messages,
    require_api_key,
    summaries,
    summary_context,
)
from app.core.config import Settings
from app.models.chat import (
    ChatMessage,
    ChatSummary,
    StartTranscriptionRequest,
    SummaryContext,
    SummarySourceStats,
    TranscriptionJob,
)


class FailingClickHouse:
    async def recent_messages(self, **_kwargs):
        raise RuntimeError("query failed")

    async def message_total(self, **_kwargs):
        raise RuntimeError("query failed")

    async def summary_context(self, **_kwargs):
        raise RuntimeError("query failed")


class CountingClickHouse:
    async def message_total(self, **kwargs):
        assert kwargs == {"channel": "example", "session_id": None}
        return 42


class InsertMessagesClickHouse:
    def __init__(self):
        self.messages = None

    async def insert_messages(self, messages):
        self.messages = messages


class InsertSummaryClickHouse:
    def __init__(self):
        self.summary = None

    async def insert_summary(self, summary):
        self.summary = summary


class SummaryClickHouse:
    async def recent_summaries(self, **kwargs):
        assert kwargs == {"channel": "example", "limit": 5}
        return ["summary"]


class SummaryContextClickHouse:
    def __init__(self, context):
        self.context = context
        self.kwargs = None

    async def summary_context(self, **kwargs):
        self.kwargs = kwargs
        return self.context


class FailingSummaryService:
    async def generate(self, **_kwargs):
        raise RuntimeError("OPENAI_API_KEY is not configured")


class FakeHub:
    def recent(self, channel=None, limit=100):
        return [
            ChatMessage(
                message_id="message-1",
                channel_id="channel-1",
                channel_login=channel or "example",
                channel_display_name="Example",
                session_id="channel-1:2026-04-28",
                session_date=date(2026, 4, 28),
                chatter_user_id="user-1",
                chatter_login="viewer",
                chatter_display_name="Viewer",
                message_text="hello",
                event_ts=datetime(2026, 4, 28, tzinfo=UTC),
            )
        ][:limit]


def chat_message() -> ChatMessage:
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


def chat_summary() -> ChatSummary:
    return ChatSummary(
        summary_id="summary-1",
        channel_id="channel-1",
        channel_login="example",
        session_id="channel-1:2026-04-28",
        window_start=datetime(2026, 4, 28, tzinfo=UTC),
        window_end=datetime(2026, 4, 28, 1, tzinfo=UTC),
        window_size="60m",
        summary_text="A short summary.",
        model="test-model",
        source_stats=SummarySourceStats(message_count=1, unique_chatter_count=1),
        created_at=datetime(2026, 4, 28, 1, tzinfo=UTC),
    )


def summary_context_payload() -> SummaryContext:
    return SummaryContext(
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        session_id="channel-1:2026-04-28",
        window_start=datetime(2026, 4, 28, tzinfo=UTC),
        window_end=datetime(2026, 4, 28, 1, tzinfo=UTC),
        window_size="60m",
        stats=SummarySourceStats(message_count=1, unique_chatter_count=1),
        sample_messages=[chat_message()],
    )


@pytest.mark.anyio
async def test_recent_messages_falls_back_to_hub_when_clickhouse_fails() -> None:
    messages = await recent_messages(
        channel="example",
        session_id=None,
        limit=150,
        clickhouse=FailingClickHouse(),
        hub=FakeHub(),
    )

    assert len(messages) == 1
    assert messages[0].message_text == "hello"


@pytest.mark.anyio
async def test_insert_messages_returns_inserted_count() -> None:
    clickhouse = InsertMessagesClickHouse()
    message = chat_message()

    result = await insert_messages(messages=[message], clickhouse=clickhouse)

    assert result.inserted == 1
    assert clickhouse.messages == [message]


@pytest.mark.anyio
async def test_message_total_returns_clickhouse_count() -> None:
    total = await message_total(
        channel="example",
        session_id=None,
        clickhouse=CountingClickHouse(),
    )

    assert total.count == 42


@pytest.mark.anyio
async def test_message_total_returns_zero_when_clickhouse_fails() -> None:
    total = await message_total(
        channel="example",
        session_id=None,
        clickhouse=FailingClickHouse(),
    )

    assert total.count == 0


@pytest.mark.anyio
async def test_summaries_returns_clickhouse_rows() -> None:
    result = await summaries(channel="example", limit=5, clickhouse=SummaryClickHouse())

    assert result == ["summary"]


@pytest.mark.anyio
async def test_summary_context_returns_clickhouse_context() -> None:
    context = summary_context_payload()
    clickhouse = SummaryContextClickHouse(context)

    result = await summary_context(channel="example", window_minutes=30, max_messages=25, clickhouse=clickhouse)

    assert result == context
    assert clickhouse.kwargs == {"channel": "example", "window_minutes": 30, "max_messages": 25}


@pytest.mark.anyio
async def test_summary_context_returns_404_when_empty() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await summary_context(
            channel="example",
            window_minutes=30,
            max_messages=25,
            clickhouse=SummaryContextClickHouse(None),
        )

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_insert_summary_returns_inserted_count() -> None:
    clickhouse = InsertSummaryClickHouse()
    summary = chat_summary()

    result = await insert_summary(summary=summary, clickhouse=clickhouse)

    assert result.inserted == 1
    assert clickhouse.summary == summary


@pytest.mark.anyio
async def test_generate_summary_returns_503_when_openai_is_missing() -> None:
    from app.models.chat import GenerateSummaryRequest

    with pytest.raises(HTTPException) as exc:
        await generate_summary(
            request=GenerateSummaryRequest(channel="example", window_minutes=60),
            service=FailingSummaryService(),
        )

    assert exc.value.status_code == 503


@pytest.mark.anyio
async def test_summary_context_returns_503_when_clickhouse_fails() -> None:
    with pytest.raises(HTTPException) as exc:
        await summary_context(
            channel="example",
            window_minutes=30,
            max_messages=25,
            clickhouse=FailingClickHouse(),
        )

    assert exc.value.status_code == 503
    assert "query failed" not in str(exc.value.detail)


@pytest.mark.anyio
async def test_insert_messages_rejects_oversized_batch() -> None:
    clickhouse = InsertMessagesClickHouse()
    messages = [chat_message()] * (MAX_MESSAGE_BATCH_SIZE + 1)

    with pytest.raises(HTTPException) as exc:
        await insert_messages(messages=messages, clickhouse=clickhouse)

    assert exc.value.status_code == 413
    assert clickhouse.messages is None


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_require_api_key_is_noop_when_token_unset() -> None:
    require_api_key(x_api_key=None, settings=_settings(api_auth_token=""))


def test_require_api_key_accepts_matching_token() -> None:
    require_api_key(x_api_key="secret-token", settings=_settings(api_auth_token="secret-token"))


def test_require_api_key_rejects_missing_or_wrong_token() -> None:
    settings = _settings(api_auth_token="secret-token")
    for provided in (None, "", "wrong-token"):
        with pytest.raises(HTTPException) as exc:
            require_api_key(x_api_key=provided, settings=settings)
        assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_start_transcription_returns_429_at_concurrency_cap(monkeypatch) -> None:
    from app.api import routes

    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: _settings(openai_api_key="test-key", transcription_max_concurrent_jobs=1),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(transcription_jobs={}, transcription_tasks={"job-1": object()})
        )
    )

    with pytest.raises(HTTPException) as exc:
        await routes.start_transcription(
            request=request,
            payload=StartTranscriptionRequest(channel="example", duration_minutes=1),
        )

    assert exc.value.status_code == 429
    assert request.app.state.transcription_jobs == {}


def _finished_job(job_id: str, status: str = "completed") -> TranscriptionJob:
    started_at = datetime(2026, 4, 28, tzinfo=UTC)
    return TranscriptionJob(
        job_id=job_id,
        channel_login="example",
        duration_seconds=60,
        status=status,
        started_at=started_at,
        ends_at=started_at + timedelta(seconds=60),
    )


def test_prune_finished_jobs_evicts_oldest_finished_only() -> None:
    jobs = {f"done-{i}": _finished_job(f"done-{i}") for i in range(60)}
    jobs["running-1"] = _finished_job("running-1", status="running")

    _prune_finished_jobs(jobs, max_finished=50)

    assert "running-1" in jobs
    finished = [job for job in jobs.values() if job.status != "running"]
    assert len(finished) == 50
    assert "done-0" not in jobs
    assert "done-9" not in jobs
    assert "done-10" in jobs
    assert "done-59" in jobs


# ---------------------------------------------------------------------------
# VOD analysis routes
# ---------------------------------------------------------------------------


def _vod_request(jobs=None, tasks=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(vod_jobs=jobs if jobs is not None else {}, vod_tasks=tasks if tasks is not None else {}))
    )


def _vod_job(video_id: str, status: str = "fetching"):
    from app.models.vod import VodAnalysisJob

    now = datetime(2026, 5, 1, tzinfo=UTC)
    return VodAnalysisJob(video_id=video_id, status=status, started_at=now, updated_at=now)


def _vod_analysis(video_id: str = "123456", status: str = "completed"):
    from app.models.vod import VodAnalysis

    now = datetime(2026, 5, 1, tzinfo=UTC)
    return VodAnalysis(
        video_id=video_id,
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        title="Big stream",
        video_created_at=now,
        duration_seconds=3000,
        bucket_seconds=5,
        message_count=100,
        unique_chatter_count=10,
        status=status,
        analyzed_at=now,
        updated_at=now,
    )


class VodClickHouse:
    def __init__(self, analysis=None, stored: int = 0, fail: bool = False, fail_activity: bool = False):
        self.analysis = analysis
        self.stored = stored
        self.fail = fail
        self.fail_activity = fail_activity
        self.activity_kwargs = None

    async def get_vod_analysis(self, video_id):
        if self.fail:
            raise RuntimeError("query failed")
        return self.analysis

    async def vod_message_count(self, session_id):
        if self.fail:
            raise RuntimeError("query failed")
        return self.stored

    async def recent_vod_analyses(self, limit=20):
        if self.fail:
            raise RuntimeError("query failed")
        return [self.analysis] if self.analysis else []

    async def vod_activity(self, **kwargs):
        self.activity_kwargs = kwargs
        if self.fail_activity:
            raise RuntimeError("query failed")
        return []


class FakeVodService:
    def __init__(self):
        self.calls = []

    async def run(self, video_id, *, skip_fetch=False):
        self.calls.append((video_id, skip_fetch))


def _parse_ok(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def _parse_bad(value: str) -> str:
    raise ValueError("bad reference")


async def _analyze(request, payload, clickhouse, service, parse=_parse_ok, **settings):
    from app.api import routes

    return await routes.analyze_vod(
        request=request,
        payload=payload,
        clickhouse=clickhouse,
        settings=_settings(**settings),
        service=service,
        parse_reference=parse,
    )


@pytest.mark.anyio
async def test_analyze_vod_returns_stored_completed_analysis_without_task() -> None:
    from app.models.vod import AnalyzeVodRequest

    request = _vod_request()
    service = FakeVodService()
    analysis = _vod_analysis()

    result = await _analyze(
        request, AnalyzeVodRequest(video="https://www.twitch.tv/videos/123456"), VodClickHouse(analysis=analysis), service
    )

    assert result.job is None
    assert result.analysis == analysis
    assert request.app.state.vod_jobs == {}
    assert request.app.state.vod_tasks == {}
    assert service.calls == []


@pytest.mark.anyio
async def test_analyze_vod_starts_task_and_skips_fetch_when_chat_is_stored() -> None:
    import asyncio

    from app.models.vod import AnalyzeVodRequest

    request = _vod_request()
    service = FakeVodService()

    result = await _analyze(
        request,
        AnalyzeVodRequest(video="123456"),
        VodClickHouse(analysis=_vod_analysis(status="failed"), stored=42),
        service,
    )

    assert result.job is not None
    assert result.job.status == "queued"
    assert "123456" in request.app.state.vod_tasks
    await request.app.state.vod_tasks["123456"]
    await asyncio.sleep(0)
    assert service.calls == [("123456", True)]
    assert request.app.state.vod_tasks == {}


@pytest.mark.anyio
async def test_analyze_vod_force_refetches_and_clickhouse_errors_are_soft() -> None:
    from app.models.vod import AnalyzeVodRequest

    for payload, clickhouse in (
        (AnalyzeVodRequest(video="123456", force=True), VodClickHouse(analysis=_vod_analysis(), stored=42)),
        (AnalyzeVodRequest(video="123456"), VodClickHouse(fail=True)),
    ):
        request = _vod_request()
        service = FakeVodService()
        result = await _analyze(request, payload, clickhouse, service)
        await request.app.state.vod_tasks["123456"]
        assert result.job is not None
        assert service.calls == [("123456", False)]


@pytest.mark.anyio
async def test_analyze_vod_returns_running_job_for_duplicate_submit() -> None:
    from app.models.vod import AnalyzeVodRequest

    running = _vod_job("123456", status="ingesting")
    request = _vod_request(jobs={"123456": running})
    service = FakeVodService()

    result = await _analyze(request, AnalyzeVodRequest(video="123456"), VodClickHouse(), service, vod_max_concurrent_jobs=1)

    assert result.job == running
    assert service.calls == []
    assert request.app.state.vod_tasks == {}


@pytest.mark.anyio
async def test_analyze_vod_returns_429_at_cap() -> None:
    from app.models.vod import AnalyzeVodRequest

    request = _vod_request(jobs={"1": _vod_job("1"), "2": _vod_job("2", status="completed")})

    with pytest.raises(HTTPException) as exc:
        await _analyze(request, AnalyzeVodRequest(video="123456"), VodClickHouse(), FakeVodService(), vod_max_concurrent_jobs=1)

    assert exc.value.status_code == 429
    assert "123456" not in request.app.state.vod_jobs


@pytest.mark.anyio
async def test_analyze_vod_returns_400_on_bad_reference() -> None:
    from app.models.vod import AnalyzeVodRequest

    request = _vod_request()
    with pytest.raises(HTTPException) as exc:
        await _analyze(request, AnalyzeVodRequest(video="nonsense"), VodClickHouse(), FakeVodService(), parse=_parse_bad)

    assert exc.value.status_code == 400
    assert request.app.state.vod_jobs == {}


@pytest.mark.anyio
async def test_vod_list_is_fail_soft() -> None:
    from app.api import routes

    assert await routes.vod_analyses(limit=5, clickhouse=VodClickHouse(fail=True)) == []
    analysis = _vod_analysis()
    assert await routes.vod_analyses(limit=5, clickhouse=VodClickHouse(analysis=analysis)) == [analysis]


@pytest.mark.anyio
async def test_vod_detail_returns_job_and_analysis_or_404() -> None:
    from app.api import routes

    with pytest.raises(HTTPException) as exc:
        await routes.vod_analysis(request=_vod_request(), video_id="123456", clickhouse=VodClickHouse())
    assert exc.value.status_code == 404

    job = _vod_job("123456")
    result = await routes.vod_analysis(
        request=_vod_request(jobs={"123456": job}), video_id="123456", clickhouse=VodClickHouse(fail=True)
    )
    assert result.job == job
    assert result.analysis is None


@pytest.mark.anyio
async def test_vod_activity_404_and_fail_soft() -> None:
    from app.api import routes

    with pytest.raises(HTTPException) as exc:
        await routes.vod_activity(video_id="123456", bucket_seconds=None, clickhouse=VodClickHouse())
    assert exc.value.status_code == 404

    clickhouse = VodClickHouse(analysis=_vod_analysis(), fail_activity=True)
    result = await routes.vod_activity(video_id="123456", bucket_seconds=None, clickhouse=clickhouse)
    assert result.buckets == []
    assert result.bucket_seconds == 5
    assert result.duration_seconds == 3000
    assert clickhouse.activity_kwargs["session_id"] == "vod:123456"

    clickhouse = VodClickHouse(analysis=_vod_analysis())
    result = await routes.vod_activity(video_id="123456", bucket_seconds=30, clickhouse=clickhouse)
    assert result.bucket_seconds == 30
    assert clickhouse.activity_kwargs["bucket_seconds"] == 30


@pytest.mark.anyio
async def test_label_vod_returns_503_without_key_and_404_without_analysis() -> None:
    from app.api import routes
    from app.services.vod_analysis import VodLabelService

    with pytest.raises(HTTPException) as exc:
        await routes.label_vod(
            video_id="123456",
            service=VodLabelService(clickhouse=VodClickHouse(analysis=_vod_analysis()), api_key="", model="m"),
        )
    assert exc.value.status_code == 503

    with pytest.raises(HTTPException) as exc:
        await routes.label_vod(
            video_id="123456",
            service=VodLabelService(clickhouse=VodClickHouse(), api_key="key", model="m"),
        )
    assert exc.value.status_code == 404


def test_prune_finished_jobs_with_custom_active_statuses() -> None:
    from app.models.vod import ACTIVE_VOD_STATUSES

    jobs = {f"done-{i}": _vod_job(f"done-{i}", status="completed") for i in range(5)}
    jobs["failed-1"] = _vod_job("failed-1", status="failed")
    for status in ("queued", "fetching", "ingesting", "analyzing"):
        jobs[status] = _vod_job(status, status=status)

    _prune_finished_jobs(jobs, max_finished=2, active_statuses=ACTIVE_VOD_STATUSES)

    assert set(jobs) == {"done-4", "failed-1", "queued", "fetching", "ingesting", "analyzing"}