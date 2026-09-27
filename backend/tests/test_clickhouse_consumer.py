import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.json import dumps
from app.workers import clickhouse_consumer
from app.workers.clickhouse_consumer import ClickHouseConsumerWorker, chat_message_from_record


def test_chat_message_from_record_parses_valid_payload() -> None:
    payload = {
        "message_id": "message-1",
        "channel_id": "channel-1",
        "channel_login": "example",
        "channel_display_name": "Example",
        "session_id": "channel-1:2026-04-28",
        "session_date": date(2026, 4, 28).isoformat(),
        "chatter_user_id": "user-1",
        "chatter_login": "viewer",
        "chatter_display_name": "Viewer",
        "message_text": "hello",
        "event_ts": datetime(2026, 4, 28, tzinfo=UTC).isoformat(),
    }
    record = SimpleNamespace(topic="twitch.chat.messages", partition=0, offset=1, value=dumps(payload).encode())

    message = chat_message_from_record(record)

    assert message is not None
    assert message.message_id == "message-1"
    assert message.message_text == "hello"
    # Payloads produced before the `source` field existed default to live chat.
    assert message.source == "live"


def test_chat_message_from_record_preserves_vod_source() -> None:
    payload = {
        "message_id": "vod-comment-1",
        "channel_id": "channel-1",
        "channel_login": "example",
        "channel_display_name": "Example",
        "session_id": "vod:123",
        "session_date": date(2026, 4, 28).isoformat(),
        "chatter_user_id": "user-1",
        "chatter_login": "viewer",
        "chatter_display_name": "Viewer",
        "message_text": "KEKW",
        "event_ts": datetime(2026, 4, 28, 12, 5, tzinfo=UTC).isoformat(),
        "source": "vod",
    }
    record = SimpleNamespace(topic="twitch.chat.messages", partition=0, offset=3, value=dumps(payload).encode())

    message = chat_message_from_record(record)

    assert message is not None
    assert message.source == "vod"
    assert message.session_id == "vod:123"


def test_chat_message_from_record_skips_invalid_payload() -> None:
    record = SimpleNamespace(topic="twitch.chat.messages", partition=0, offset=2, value=b'{"message_id":"bad"}')

    assert chat_message_from_record(record) is None




class FakeRepo:
    def __init__(self, events: list[str], schema_failures: int = 0, insert_failures: int = 0) -> None:
        self.events = events
        self.schema_failures = schema_failures
        self.insert_failures = insert_failures
        self.schema_calls = 0
        self.insert_calls = 0
        self.inserted_batches: list[list[str]] = []

    async def ensure_vod_schema(self) -> None:
        self.schema_calls += 1
        self.events.append("schema")
        if self.schema_failures > 0:
            self.schema_failures -= 1
            raise RuntimeError("schema unavailable")

    async def insert_messages(self, messages: list[Any]) -> None:
        self.insert_calls += 1
        self.events.append("insert")
        self.inserted_batches.append([message.message_id for message in messages])
        if self.insert_failures > 0:
            self.insert_failures -= 1
            raise RuntimeError("no such column: source")


class FakeConsumer:
    """Hands out one queued batch per getmany() call.

    The worker is stopped on the first successful commit, or once the queued batches
    run out, so every test terminates instead of hanging. With ``repeat`` the last batch
    is returned on every call (never drained).
    """

    def __init__(
        self,
        worker: ClickHouseConsumerWorker,
        events: list[str],
        batches: list[list[Any]],
        repeat: bool = False,
        commit_failures: int = 0,
    ) -> None:
        self._worker = worker
        self._events = events
        self._batches = list(batches)
        self._repeat = repeat
        self._commit_failures = commit_failures
        self.getmany_calls = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self._events.append("stop")

    async def getmany(self, timeout_ms: int, max_records: int) -> dict[str, list[Any]]:
        # Yield to the loop like the real consumer, so a busy-looping worker can
        # still be cancelled by wait_for instead of hanging the suite.
        await asyncio.sleep(0)
        self.getmany_calls += 1
        if not self._batches:
            self._worker.stop()
            return {}
        records = self._batches[0] if self._repeat else self._batches.pop(0)
        return {"tp": records} if records else {}

    async def commit(self) -> None:
        self._events.append("commit")
        if self._commit_failures > 0:
            self._commit_failures -= 1
            raise RuntimeError("CommitFailedError: rebalance in progress")
        self._worker.stop()


def _bare_worker(repo: FakeRepo | None = None, flush_interval_seconds: float = 0.0) -> ClickHouseConsumerWorker:
    worker = ClickHouseConsumerWorker.__new__(ClickHouseConsumerWorker)
    worker._settings = SimpleNamespace(
        clickhouse_host="localhost",
        clickhouse_port=8123,
        clickhouse_username="default",
        clickhouse_password="",
        clickhouse_database="twitch_analyze",
    )
    worker._batch_size = 10
    worker._flush_interval_seconds = flush_interval_seconds
    worker._stop = asyncio.Event()
    worker._repo = repo
    return worker


def _patch_connect(monkeypatch: pytest.MonkeyPatch, repo: FakeRepo) -> None:
    async def fake_connect(cls: Any, **kwargs: Any) -> FakeRepo:
        return repo

    monkeypatch.setattr(clickhouse_consumer.ClickHouseRepository, "connect", classmethod(fake_connect))


def _record(offset: int) -> SimpleNamespace:
    payload = {
        "message_id": f"message-{offset}",
        "channel_id": "channel-1",
        "channel_login": "example",
        "channel_display_name": "Example",
        "session_id": "channel-1:2026-04-28",
        "session_date": date(2026, 4, 28).isoformat(),
        "chatter_user_id": "user-1",
        "chatter_login": "viewer",
        "chatter_display_name": "Viewer",
        "message_text": "hello",
        "event_ts": datetime(2026, 4, 28, tzinfo=UTC).isoformat(),
    }
    return SimpleNamespace(topic="twitch.chat.messages", partition=0, offset=offset, value=dumps(payload).encode())


def _invalid_record(offset: int) -> SimpleNamespace:
    return SimpleNamespace(topic="twitch.chat.messages", partition=0, offset=offset, value=b'{"message_id":"bad"}')


@pytest.mark.anyio
async def test_ensure_schema_with_retry_recovers_after_failures() -> None:
    repo = FakeRepo([], schema_failures=2)
    worker = _bare_worker(repo)

    await worker._ensure_schema_with_retry(attempts=5, delay_seconds=0)

    assert repo.schema_calls == 3


@pytest.mark.anyio
async def test_ensure_schema_with_retry_raises_after_last_attempt() -> None:
    repo = FakeRepo([], schema_failures=10)
    worker = _bare_worker(repo)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        await worker._ensure_schema_with_retry(attempts=3, delay_seconds=0)

    assert repo.schema_calls == 3


@pytest.mark.anyio
async def test_run_reapplies_schema_before_retrying_failed_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = FakeRepo(events, insert_failures=1)
    worker = _bare_worker()
    worker._consumer = FakeConsumer(worker, events, [[_record(1)]])
    _patch_connect(monkeypatch, repo)

    await asyncio.wait_for(worker.run(), timeout=5)

    # startup migration, failed insert (no commit), best-effort migration, successful
    # insert, then the offsets are committed, and finally the consumer is stopped.
    assert events == ["schema", "insert", "schema", "insert", "commit", "stop"]
    assert repo.inserted_batches == [["message-1"], ["message-1"]]


@pytest.mark.anyio
async def test_run_does_not_fetch_while_insert_retry_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = FakeRepo(events, insert_failures=3)
    worker = _bare_worker()
    # Kafka always has more records available.
    consumer = FakeConsumer(worker, events, [[_record(1)]], repeat=True)
    worker._consumer = consumer
    _patch_connect(monkeypatch, repo)

    await asyncio.wait_for(worker.run(), timeout=5)

    # Only the initial fetch: while the batch awaits a retry nothing more is pulled, so
    # the in-memory batch stays bounded and is retried unchanged.
    assert consumer.getmany_calls == 1
    assert repo.inserted_batches == [["message-1"]] * 4
    assert events == ["schema"] + ["insert", "schema"] * 3 + ["insert", "commit", "stop"]


@pytest.mark.anyio
async def test_run_flushes_pending_batch_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = FakeRepo(events)
    # A long flush interval keeps the records buffered until shutdown.
    worker = _bare_worker(flush_interval_seconds=3600)
    worker._consumer = FakeConsumer(worker, events, [[_record(1), _record(2)]])
    _patch_connect(monkeypatch, repo)

    await asyncio.wait_for(worker.run(), timeout=5)

    assert repo.inserted_batches == [["message-1", "message-2"]]
    assert events == ["schema", "insert", "commit", "stop"]


@pytest.mark.anyio
async def test_run_leaves_offsets_uncommitted_when_final_flush_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = FakeRepo(events, insert_failures=1)
    worker = _bare_worker(flush_interval_seconds=3600)
    worker._consumer = FakeConsumer(worker, events, [[_record(1), _record(2)]])
    _patch_connect(monkeypatch, repo)

    await asyncio.wait_for(worker.run(), timeout=5)

    # The failed final insert is logged, offsets stay uncommitted (records are replayed
    # on restart) and the consumer is still stopped.
    assert events == ["schema", "insert", "stop"]


@pytest.mark.anyio
async def test_run_commits_batch_of_only_invalid_records(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = FakeRepo(events)
    worker = _bare_worker()
    worker._consumer = FakeConsumer(worker, events, [[_invalid_record(1), _invalid_record(2)]])
    _patch_connect(monkeypatch, repo)

    await asyncio.wait_for(worker.run(), timeout=5)

    # Poison records are skipped and their offsets committed so they are not re-read forever.
    assert repo.insert_calls == 0
    assert events == ["schema", "commit", "stop"]


@pytest.mark.anyio
async def test_run_survives_commit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = FakeRepo(events)
    worker = _bare_worker()
    worker._consumer = FakeConsumer(worker, events, [[_record(1)], [_record(2)]], commit_failures=1)
    _patch_connect(monkeypatch, repo)

    await asyncio.wait_for(worker.run(), timeout=5)

    # The failed commit is swallowed; the worker keeps consuming and commits the next batch.
    assert repo.inserted_batches == [["message-1"], ["message-2"]]
    assert events == ["schema", "insert", "commit", "insert", "commit", "stop"]


@pytest.mark.anyio
async def test_run_exits_when_schema_migration_never_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    repo = FakeRepo(events, schema_failures=100)
    worker = _bare_worker()
    worker._consumer = FakeConsumer(worker, events, [])

    original_ensure = ClickHouseConsumerWorker._ensure_schema_with_retry

    async def fast_ensure(self: ClickHouseConsumerWorker) -> None:
        await original_ensure(self, attempts=2, delay_seconds=0)

    _patch_connect(monkeypatch, repo)
    monkeypatch.setattr(ClickHouseConsumerWorker, "_ensure_schema_with_retry", fast_ensure)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        await asyncio.wait_for(worker.run(), timeout=5)

    assert repo.schema_calls == 2
    assert repo.insert_calls == 0
    assert events == ["schema", "schema"]
