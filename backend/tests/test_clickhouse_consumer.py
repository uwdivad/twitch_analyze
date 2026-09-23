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
    def __init__(self, schema_failures: int = 0, insert_failures: int = 0) -> None:
        self.schema_failures = schema_failures
        self.insert_failures = insert_failures
        self.schema_calls = 0
        self.insert_calls = 0
        self.events: list[str] = []

    async def ensure_vod_schema(self) -> None:
        self.schema_calls += 1
        self.events.append("schema")
        if self.schema_failures > 0:
            self.schema_failures -= 1
            raise RuntimeError("schema unavailable")

    async def insert_messages(self, messages: list[Any]) -> None:
        self.insert_calls += 1
        self.events.append("insert")
        if self.insert_failures > 0:
            self.insert_failures -= 1
            raise RuntimeError("no such column: source")


class FakeConsumer:
    def __init__(self, worker: ClickHouseConsumerWorker, records: list[Any]) -> None:
        self._worker = worker
        self._records = records
        self.commits = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def getmany(self, timeout_ms: int, max_records: int) -> dict[str, list[Any]]:
        records, self._records = self._records, []
        return {"tp": records} if records else {}

    async def commit(self) -> None:
        self.commits += 1
        self._worker.stop()


def _bare_worker(repo: FakeRepo | None = None) -> ClickHouseConsumerWorker:
    worker = ClickHouseConsumerWorker.__new__(ClickHouseConsumerWorker)
    worker._settings = SimpleNamespace(
        clickhouse_host="localhost",
        clickhouse_port=8123,
        clickhouse_username="default",
        clickhouse_password="",
        clickhouse_database="twitch_analyze",
    )
    worker._batch_size = 10
    worker._flush_interval_seconds = 0.0
    worker._stop = asyncio.Event()
    worker._repo = repo
    return worker


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


@pytest.mark.anyio
async def test_ensure_schema_with_retry_recovers_after_failures() -> None:
    repo = FakeRepo(schema_failures=2)
    worker = _bare_worker(repo)

    await worker._ensure_schema_with_retry(attempts=5, delay_seconds=0)

    assert repo.schema_calls == 3


@pytest.mark.anyio
async def test_ensure_schema_with_retry_raises_after_last_attempt() -> None:
    repo = FakeRepo(schema_failures=10)
    worker = _bare_worker(repo)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        await worker._ensure_schema_with_retry(attempts=3, delay_seconds=0)

    assert repo.schema_calls == 3


@pytest.mark.anyio
async def test_run_reapplies_schema_before_retrying_failed_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo(insert_failures=1)
    worker = _bare_worker()
    consumer = FakeConsumer(worker, [_record(1)])
    worker._consumer = consumer

    async def fake_connect(cls: Any, **kwargs: Any) -> FakeRepo:
        return repo

    monkeypatch.setattr(clickhouse_consumer.ClickHouseRepository, "connect", classmethod(fake_connect))

    await asyncio.wait_for(worker.run(), timeout=5)

    # startup migration, failed insert, best-effort migration, successful insert
    assert repo.events == ["schema", "insert", "schema", "insert"]
    assert consumer.commits == 1


@pytest.mark.anyio
async def test_run_exits_when_schema_migration_never_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo(schema_failures=100)
    worker = _bare_worker()
    worker._consumer = FakeConsumer(worker, [])

    async def fake_connect(cls: Any, **kwargs: Any) -> FakeRepo:
        return repo

    original_ensure = ClickHouseConsumerWorker._ensure_schema_with_retry

    async def fast_ensure(self: ClickHouseConsumerWorker) -> None:
        await original_ensure(self, attempts=2, delay_seconds=0)

    monkeypatch.setattr(clickhouse_consumer.ClickHouseRepository, "connect", classmethod(fake_connect))
    monkeypatch.setattr(ClickHouseConsumerWorker, "_ensure_schema_with_retry", fast_ensure)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        await worker.run()

    assert repo.schema_calls == 2
    assert repo.insert_calls == 0