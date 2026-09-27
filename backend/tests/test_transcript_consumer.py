from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.json import dumps
from app.workers.transcript_consumer import transcript_segment_from_record


def test_transcript_segment_from_record_parses_valid_payload() -> None:
    payload = {
        "segment_id": "segment-1",
        "channel_login": "example",
        "session_id": "example:2026-05-07",
        "segment_started_at": datetime(2026, 5, 7, 15, 30, tzinfo=UTC).isoformat(),
        "segment_ended_at": datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC).isoformat(),
        "audio_path": "/tmp/twitch-audio/example/20260507T153000Z.wav",
        "transcript_text": "hello chat",
        "model": "gpt-4o-mini-transcribe",
        "status": "transcribed",
    }
    record = SimpleNamespace(topic="twitch.stream.transcripts", partition=0, offset=1, value=dumps(payload).encode())

    segment = transcript_segment_from_record(record)

    assert segment is not None
    assert segment.segment_id == "segment-1"
    assert segment.transcript_text == "hello chat"


def test_transcript_segment_from_record_skips_invalid_payload() -> None:
    record = SimpleNamespace(topic="twitch.stream.transcripts", partition=0, offset=2, value=b'{"segment_id":"bad"}')

    assert transcript_segment_from_record(record) is None


# ---------------------------------------------------------------------------
# Run loop: offsets are committed only after a successful ClickHouse insert.
# Imports are kept with this section so it can be appended without touching the
# module header.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402

from app.workers import transcript_consumer  # noqa: E402
from app.workers.transcript_consumer import TranscriptConsumerWorker  # noqa: E402


class LoopRepo:
    def __init__(self, events: list[str], insert_failures: int = 0) -> None:
        self.events = events
        self.insert_failures = insert_failures
        self.inserted: list[list[Any]] = []

    async def ensure_transcript_segments_table(self) -> None:
        return None

    async def insert_transcript_segments(self, segments: list[Any]) -> None:
        if self.insert_failures > 0:
            self.insert_failures -= 1
            self.events.append("insert_failed")
            raise RuntimeError("clickhouse unavailable")
        self.events.append("insert")
        self.inserted.append(list(segments))


class LoopConsumer:
    """Serves queued batches; stops the worker on commit and/or once drained."""

    def __init__(
        self,
        worker: TranscriptConsumerWorker,
        events: list[str],
        batches: list[list[Any]],
        *,
        stop_on_commit: bool = True,
        stop_when_drained: bool = False,
    ) -> None:
        self._worker = worker
        self.events = events
        self._batches = list(batches)
        self._stop_on_commit = stop_on_commit
        self._stop_when_drained = stop_when_drained

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.events.append("consumer_stop")

    async def getmany(self, timeout_ms: int, max_records: int) -> dict[str, list[Any]]:
        await asyncio.sleep(0)
        self.events.append("getmany")
        records = self._batches.pop(0) if self._batches else []
        if not self._batches and self._stop_when_drained:
            self._worker.stop()
        return {"tp": records} if records else {}

    async def commit(self) -> None:
        self.events.append("commit")
        if self._stop_on_commit:
            self._worker.stop()


def _segment_record(offset: int) -> SimpleNamespace:
    payload = {
        "segment_id": f"segment-{offset}",
        "channel_login": "example",
        "session_id": "example:2026-05-07",
        "segment_started_at": datetime(2026, 5, 7, 15, 30, tzinfo=UTC).isoformat(),
        "segment_ended_at": datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC).isoformat(),
        "audio_path": "/tmp/twitch-audio/example/20260507T153000Z.wav",
        "transcript_text": "hello chat",
        "model": "gpt-4o-mini-transcribe",
        "status": "transcribed",
    }
    return SimpleNamespace(
        topic="twitch.stream.transcripts", partition=0, offset=offset, value=dumps(payload).encode()
    )


def _loop_worker(
    monkeypatch: pytest.MonkeyPatch,
    repo: LoopRepo,
    *,
    batch_size: int = 10,
    flush_interval_seconds: float = 0.0,
) -> TranscriptConsumerWorker:
    worker = TranscriptConsumerWorker.__new__(TranscriptConsumerWorker)
    worker._settings = SimpleNamespace(
        clickhouse_host="localhost",
        clickhouse_port=8123,
        clickhouse_username="default",
        clickhouse_password="",
        clickhouse_database="twitch_analyze",
    )
    worker._batch_size = batch_size
    worker._flush_interval_seconds = flush_interval_seconds
    worker._stop = asyncio.Event()
    worker._repo = None

    async def fake_connect(cls: Any, **kwargs: Any) -> LoopRepo:
        return repo

    monkeypatch.setattr(transcript_consumer.ClickHouseRepository, "connect", classmethod(fake_connect))
    return worker


@pytest.mark.anyio
async def test_run_commits_only_after_successful_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = LoopRepo(events)
    worker = _loop_worker(monkeypatch, repo)
    worker._consumer = LoopConsumer(worker, events, [[_segment_record(1), _segment_record(2)]])

    await asyncio.wait_for(worker.run(), timeout=5)

    assert events == ["getmany", "insert", "commit", "consumer_stop"]
    assert [segment.segment_id for segment in repo.inserted[0]] == ["segment-1", "segment-2"]


@pytest.mark.anyio
async def test_run_retries_failed_insert_without_committing_or_fetching(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = LoopRepo(events, insert_failures=2)
    worker = _loop_worker(monkeypatch, repo)
    worker._consumer = LoopConsumer(worker, events, [[_segment_record(1)]])

    await asyncio.wait_for(worker.run(), timeout=5)

    # No commit follows a failed insert, and no new records are fetched while a retry
    # is pending; the same batch is re-inserted and only then committed.
    assert events == ["getmany", "insert_failed", "insert_failed", "insert", "commit", "consumer_stop"]
    assert [[segment.segment_id for segment in batch] for batch in repo.inserted] == [["segment-1"]]


@pytest.mark.anyio
async def test_run_flushes_pending_batch_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = LoopRepo(events)
    # Neither the size nor the time trigger fires inside the loop.
    worker = _loop_worker(monkeypatch, repo, batch_size=10, flush_interval_seconds=1000.0)
    worker._consumer = LoopConsumer(
        worker, events, [[_segment_record(1)]], stop_on_commit=False, stop_when_drained=True
    )

    await asyncio.wait_for(worker.run(), timeout=5)

    assert events == ["getmany", "insert", "commit", "consumer_stop"]
    assert [segment.segment_id for segment in repo.inserted[0]] == ["segment-1"]


@pytest.mark.anyio
async def test_run_leaves_offsets_uncommitted_when_final_flush_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = LoopRepo(events, insert_failures=1)
    worker = _loop_worker(monkeypatch, repo, batch_size=10, flush_interval_seconds=1000.0)
    worker._consumer = LoopConsumer(
        worker, events, [[_segment_record(1)]], stop_on_commit=False, stop_when_drained=True
    )

    await asyncio.wait_for(worker.run(), timeout=5)

    assert events == ["getmany", "insert_failed", "consumer_stop"]


@pytest.mark.anyio
async def test_run_commits_batch_of_only_invalid_records_without_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = LoopRepo(events)
    worker = _loop_worker(monkeypatch, repo)
    bad = SimpleNamespace(topic="twitch.stream.transcripts", partition=0, offset=9, value=b'{"segment_id":"bad"}')
    worker._consumer = LoopConsumer(worker, events, [[bad]])

    await asyncio.wait_for(worker.run(), timeout=5)

    # Skipped poison records are committed so they are not replayed forever.
    assert events == ["getmany", "commit", "consumer_stop"]
    assert repo.inserted == []
