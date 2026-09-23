import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.json import loads
from app.models.chat import ChatMessage, TopItem
from app.models.vod import VodAnalysis, VodPeak
from app.storage.clickhouse import (
    VOD_ANALYSIS_COLUMNS,
    ClickHouseRepository,
    _fill_activity_buckets,
)
from app.storage.kafka import KafkaJsonProducer

SOURCE_INDEX = 19


class FakeClickHouseClient:
    """Records calls and returns canned query results; never talks to ClickHouse."""

    def __init__(self, query_results: list[list[tuple[Any, ...]]] | None = None, engine_full: str = "") -> None:
        self.query_results = list(query_results or [])
        self.engine_full = engine_full
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.inserts: list[tuple[str, list[tuple[Any, ...]], list[str]]] = []

    def query(self, query: str, params: dict[str, Any]) -> SimpleNamespace:
        self.queries.append((query, params))
        rows = self.query_results.pop(0) if self.query_results else []
        return SimpleNamespace(result_rows=rows)

    def command(self, cmd: str, **kwargs: Any) -> Any:
        self.commands.append((cmd, kwargs))
        if "system.tables" in cmd:
            return self.engine_full
        return None

    def insert(self, table: str, rows: list[tuple[Any, ...]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))


def _repo(client: FakeClickHouseClient) -> ClickHouseRepository:
    repo = ClickHouseRepository.__new__(ClickHouseRepository)
    repo._database = "twitch_analyze"
    repo._lock = asyncio.Lock()
    repo._client = client
    return repo


def _message(source: str = "live") -> ChatMessage:
    return ChatMessage(
        message_id="message-1",
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        session_id="vod:123" if source == "vod" else "channel-1:2026-04-28",
        session_date=date(2026, 4, 28),
        chatter_user_id="user-1",
        chatter_login="viewer",
        chatter_display_name="Viewer",
        message_text="hello",
        event_ts=datetime(2026, 4, 28, 12, tzinfo=UTC),
        received_at=datetime(2026, 9, 1, tzinfo=UTC),
        source=source,
    )


def _analysis() -> VodAnalysis:
    return VodAnalysis(
        video_id="123",
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        title="Big stream",
        video_created_at=datetime(2026, 4, 28, 12, tzinfo=UTC),
        duration_seconds=3600,
        bucket_seconds=10,
        message_count=500,
        unique_chatter_count=42,
        status="completed",
        peaks=[
            VodPeak(
                peak_id=1,
                start_seconds=100,
                end_seconds=160,
                peak_seconds=130,
                message_count=80,
                peak_bucket_count=30,
                messages_per_second=1.5,
                score=4.2,
                baseline=0.3,
                top_emotes=[TopItem(value="KEKW", count=12)],
                top_tokens=[TopItem(value="clip", count=7)],
                sample_messages=["Viewer: clip it"],
                label="KEKW",
                title="Funny moment",
            )
        ],
        label_model="gpt-test",
        analyzed_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, 10, 5, tzinfo=UTC),
    )


def test_message_row_includes_source() -> None:
    repo = _repo(FakeClickHouseClient())
    row = repo._message_row(_message("vod"))

    assert len(row) == 20
    assert row[SOURCE_INDEX] == "vod"


def test_message_row_round_trips_through_message_from_row() -> None:
    repo = _repo(FakeClickHouseClient())
    message = _message("vod")

    restored = repo._message_from_row(repo._message_row(message))

    assert restored.source == "vod"
    assert restored.message_id == message.message_id
    assert restored.received_at == message.received_at


def test_message_from_row_defaults_blank_source_to_live() -> None:
    repo = _repo(FakeClickHouseClient())
    row = list(repo._message_row(_message("vod")))
    row[SOURCE_INDEX] = ""

    assert repo._message_from_row(tuple(row)).source == "live"


def test_message_filters_default_to_live_source() -> None:
    repo = _repo(FakeClickHouseClient())

    where, params = repo._message_filters(channel="Example", session_id=None)

    assert where == "WHERE source = %(source)s AND channel_login = %(channel)s"
    assert params == {"source": "live", "channel": "example"}


def test_message_filters_with_vod_source() -> None:
    repo = _repo(FakeClickHouseClient())

    where, params = repo._message_filters(channel=None, session_id="vod:123", source="vod")

    assert where == "WHERE source = %(source)s AND session_id = %(session_id)s"
    assert params == {"source": "vod", "session_id": "vod:123"}


def test_message_filters_none_source_disables_filter() -> None:
    repo = _repo(FakeClickHouseClient())

    assert repo._message_filters(channel=None, session_id=None, source=None) == ("", {})
    where, params = repo._message_filters(channel="example", session_id=None, source=None)
    assert "source" not in where
    assert "source" not in params


@pytest.mark.anyio
async def test_dashboard_queries_exclude_vod_chat() -> None:
    client = FakeClickHouseClient()
    repo = _repo(client)

    await repo.recent_messages()
    await repo.volume_by_minute()
    await repo.volume_by_minute_for_channels()
    await repo.message_total()
    await repo.top_chatters()
    await repo.top_emotes()

    assert len(client.queries) == 6
    for query, params in client.queries:
        assert "source = %(source)s" in query or "source = 'live'" in query
        if "%(source)s" in query:
            assert params["source"] == "live"


@pytest.mark.anyio
async def test_summary_context_queries_exclude_vod_chat() -> None:
    stats_row = ("channel-1", "example", "Example", "channel-1:2026-04-28", 5, 2)
    client = FakeClickHouseClient(query_results=[[stats_row], [], [], [], []])
    repo = _repo(client)

    context = await repo.summary_context(channel="example", window_minutes=10, max_messages=10)

    assert context is not None
    assert len(client.queries) == 5
    for query, params in client.queries:
        assert "source = %(source)s" in query
        assert params["source"] == "live"


def test_vod_analysis_row_round_trip_preserves_peaks() -> None:
    repo = _repo(FakeClickHouseClient())
    analysis = _analysis()

    row = repo._vod_analysis_row(analysis)

    assert len(row) == len(VOD_ANALYSIS_COLUMNS)
    peaks_json = row[VOD_ANALYSIS_COLUMNS.index("peaks")]
    assert isinstance(peaks_json, str)
    assert loads(peaks_json)[0]["label"] == "KEKW"
    assert row[VOD_ANALYSIS_COLUMNS.index("updated_at")] == analysis.updated_at

    restored = repo._vod_analysis_from_row(row)
    assert restored == analysis
    assert repo._vod_analysis_row(restored) == row


def test_vod_analysis_from_row_handles_naive_datetimes_and_empty_peaks() -> None:
    repo = _repo(FakeClickHouseClient())
    row = list(repo._vod_analysis_row(_analysis()))
    row[VOD_ANALYSIS_COLUMNS.index("peaks")] = ""
    row[VOD_ANALYSIS_COLUMNS.index("analyzed_at")] = datetime(2026, 9, 1, 10)

    restored = repo._vod_analysis_from_row(tuple(row))

    assert restored.peaks == []
    assert restored.analyzed_at == datetime(2026, 9, 1, 10, tzinfo=UTC)


@pytest.mark.anyio
async def test_upsert_and_get_vod_analysis_use_fake_client() -> None:
    client = FakeClickHouseClient()
    repo = _repo(client)
    analysis = _analysis()

    await repo.upsert_vod_analysis(analysis)

    table, rows, columns = client.inserts[0]
    assert table == "vod_analyses"
    assert columns == list(VOD_ANALYSIS_COLUMNS)

    client.query_results.append(rows)
    assert await repo.get_vod_analysis("123") == analysis
    query, params = client.queries[-1]
    assert "ORDER BY updated_at DESC" in query
    assert params == {"id": "123"}

    assert await repo.get_vod_analysis("missing") is None

    client.query_results.append(rows)
    recent = await repo.recent_vod_analyses(limit=5)
    assert recent == [analysis]
    assert "LIMIT 1 BY video_id" in client.queries[-1][0]
    assert client.queries[-1][1] == {"limit": 5}


def test_fill_activity_buckets_zero_fills_gaps_and_drops_out_of_range() -> None:
    rows = [(-1, 99, 99), (0, 3, 2), (2, 5, 4), (4, 1, 1), (5, 50, 50)]

    buckets = _fill_activity_buckets(rows, duration_seconds=45, bucket_seconds=10)

    # ceil(45 / 10) == 5 buckets; the last one (40-45s) is partial but included.
    assert [bucket.index for bucket in buckets] == [0, 1, 2, 3, 4]
    assert [bucket.offset_seconds for bucket in buckets] == [0, 10, 20, 30, 40]
    assert [bucket.message_count for bucket in buckets] == [3, 0, 5, 0, 1]
    assert [bucket.unique_chatter_count for bucket in buckets] == [2, 0, 4, 0, 1]


def test_fill_activity_buckets_handles_empty_and_invalid_inputs() -> None:
    assert _fill_activity_buckets([], duration_seconds=0, bucket_seconds=10) == []
    assert _fill_activity_buckets([], duration_seconds=30, bucket_seconds=0) == []
    assert [b.message_count for b in _fill_activity_buckets([], duration_seconds=30, bucket_seconds=10)] == [0, 0, 0]


@pytest.mark.anyio
async def test_vod_activity_queries_vod_source_and_fills() -> None:
    client = FakeClickHouseClient(query_results=[[(1, 7, 3)]])
    repo = _repo(client)
    created_at = datetime(2026, 4, 28, 12, tzinfo=UTC)

    buckets = await repo.vod_activity(
        session_id="vod:123", created_at=created_at, duration_seconds=30, bucket_seconds=10
    )

    assert [b.message_count for b in buckets] == [0, 7, 0]
    query, params = client.queries[0]
    assert "source = 'vod'" in query
    assert params["created_ms"] == int(created_at.timestamp() * 1000)
    assert params["bucket_ms"] == 10_000
    assert params["session_id"] == "vod:123"


@pytest.mark.anyio
async def test_vod_peak_context_shapes_results() -> None:
    client = FakeClickHouseClient(
        query_results=[
            [(12, 9)],
            [("KEKW", 4)],
            [("clip", 3), ("lol", 2)],
            [("Viewer", "viewer", "clip it"), ("", "lurker", "lol")],
        ]
    )
    repo = _repo(client)

    count, unique, emotes, tokens, samples = await repo.vod_peak_context(
        session_id="vod:123",
        window_start=datetime(2026, 4, 28, 12, tzinfo=UTC),
        window_end=datetime(2026, 4, 28, 12, 1, tzinfo=UTC),
    )

    assert (count, unique) == (12, 9)
    assert emotes == [TopItem(value="KEKW", count=4)]
    assert [token.value for token in tokens] == ["clip", "lol"]
    assert samples == ["Viewer: clip it", "lurker: lol"]
    assert len(client.queries) == 4
    for query, _params in client.queries:
        assert "source = 'vod'" in query
    assert client.queries[1][1]["top_limit"] == 5
    assert client.queries[2][1]["token_limit"] == 40
    assert client.queries[3][1]["sample_limit"] == 20


@pytest.mark.anyio
async def test_vod_message_count_filters_vod_source() -> None:
    client = FakeClickHouseClient(query_results=[[(42,)]])
    repo = _repo(client)

    assert await repo.vod_message_count("vod:123") == 42
    query, params = client.queries[0]
    assert "source = 'vod'" in query
    assert params == {"session_id": "vod:123"}


@pytest.mark.anyio
async def test_ensure_vod_schema_migrates_old_table() -> None:
    client = FakeClickHouseClient(engine_full="ReplacingMergeTree(inserted_at) TTL toDateTime(event_ts) + toIntervalDay(180)")
    repo = _repo(client)

    await repo.ensure_vod_schema()

    commands = [cmd for cmd, _kwargs in client.commands]
    assert any("ADD COLUMN IF NOT EXISTS source" in cmd for cmd in commands)
    assert any("MODIFY TTL toDateTime(received_at)" in cmd for cmd in commands)
    assert any("CREATE TABLE IF NOT EXISTS vod_analyses" in cmd for cmd in commands)


@pytest.mark.anyio
async def test_ensure_vod_schema_skips_ttl_when_already_migrated() -> None:
    client = FakeClickHouseClient(
        engine_full="ReplacingMergeTree(inserted_at) TTL toDateTime(received_at) + toIntervalDay(180)"
    )
    repo = _repo(client)

    await repo.ensure_vod_schema()

    assert not any("MODIFY TTL" in cmd for cmd, _kwargs in client.commands)


@pytest.mark.anyio
async def test_flush_on_unstarted_producer_is_noop() -> None:
    producer = KafkaJsonProducer(bootstrap_servers="localhost:9092", topic="test")

    await producer.flush()


@pytest.mark.anyio
async def test_flush_awaits_started_producer() -> None:
    flushed: list[bool] = []

    class FakeProducer:
        async def flush(self) -> None:
            flushed.append(True)

    producer = KafkaJsonProducer(bootstrap_servers="localhost:9092", topic="test")
    producer._producer = FakeProducer()  # type: ignore[assignment]

    await producer.flush()

    assert flushed == [True]
