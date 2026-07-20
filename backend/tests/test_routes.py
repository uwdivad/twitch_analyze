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
