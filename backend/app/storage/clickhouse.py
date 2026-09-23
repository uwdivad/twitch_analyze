import asyncio
import logging
import math
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import clickhouse_connect

from app.core.json import dumps, loads
from app.core.metrics import (
    CLICKHOUSE_BATCH_SIZE,
    CLICKHOUSE_INSERT_ERRORS,
    CLICKHOUSE_INSERT_LATENCY,
    CLICKHOUSE_ROWS_INSERTED,
)
from app.models.chat import (
    ChatMessage,
    ChatSummary,
    SpikeWindow,
    SummaryContext,
    SummarySourceStats,
    TopItem,
    TranscriptSegment,
    VolumePoint,
)
from app.models.vod import VodActivityBucket, VodAnalysis, VodPeak

logger = logging.getLogger(__name__)

VOD_ANALYSIS_COLUMNS: tuple[str, ...] = (
    "video_id",
    "channel_id",
    "channel_login",
    "channel_display_name",
    "title",
    "video_created_at",
    "duration_seconds",
    "bucket_seconds",
    "message_count",
    "unique_chatter_count",
    "status",
    "error",
    "peaks",
    "label_model",
    "analyzed_at",
    "updated_at",
)

CHAT_MESSAGES_TTL_EXPRESSION = "toDateTime(received_at) + INTERVAL 180 DAY DELETE"

VOD_ANALYSES_DDL = """
    CREATE TABLE IF NOT EXISTS vod_analyses
    (
        video_id String,
        channel_id String,
        channel_login LowCardinality(String),
        channel_display_name String,
        title String,
        video_created_at DateTime64(3, 'UTC'),
        duration_seconds UInt32,
        bucket_seconds UInt16,
        message_count UInt32,
        unique_chatter_count UInt32,
        status LowCardinality(String),
        error String,
        peaks String,
        label_model LowCardinality(String) DEFAULT '',
        analyzed_at DateTime64(3, 'UTC'),
        updated_at DateTime64(3, 'UTC') DEFAULT now64(3)
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY video_id
"""


def _to_unix_ms(value: datetime) -> int:
    """UTC epoch milliseconds; naive datetimes are treated as UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(round(value.timestamp() * 1000))


def _fill_activity_buckets(
    rows: Iterable[Sequence[Any]],
    duration_seconds: int,
    bucket_seconds: int,
) -> list[VodActivityBucket]:
    """Turn sparse ``(bucket_index, message_count, unique_chatter_count)`` rows
    into a dense, zero-filled bucket list covering ``[0, duration_seconds)``.

    The last bucket may be partial (``ceil``). Negative indices and indices past
    the end of the VOD are dropped.
    """
    if duration_seconds <= 0 or bucket_seconds <= 0:
        return []
    bucket_total = math.ceil(duration_seconds / bucket_seconds)
    by_index: dict[int, tuple[int, int]] = {}
    for row in rows:
        index = int(row[0])
        if 0 <= index < bucket_total:
            by_index[index] = (int(row[1]), int(row[2]))
    buckets: list[VodActivityBucket] = []
    for index in range(bucket_total):
        message_count, unique_chatter_count = by_index.get(index, (0, 0))
        buckets.append(
            VodActivityBucket(
                index=index,
                offset_seconds=index * bucket_seconds,
                message_count=message_count,
                unique_chatter_count=unique_chatter_count,
            )
        )
    return buckets


class ClickHouseRepository:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
    ) -> None:
        self._database = database
        self._lock = asyncio.Lock()
        self._client = self._connect_with_retry(
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
        )

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
    ) -> "ClickHouseRepository":
        """Async factory: runs the blocking connect/retry loop in a worker thread.

        Use this from async contexts (FastAPI lifespan, workers) instead of
        calling ``ClickHouseRepository(...)`` directly, so the retry loop's
        ``time.sleep`` never blocks the event loop. ``__init__`` remains
        available for sync/test callers.
        """
        return await asyncio.to_thread(
            cls,
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
        )

    async def insert_messages(self, messages: list[ChatMessage]) -> None:
        if not messages:
            return
        rows = [self._message_row(message) for message in messages]
        columns = [
            "message_id",
            "eventsub_message_id",
            "channel_id",
            "channel_login",
            "channel_display_name",
            "session_id",
            "session_date",
            "chatter_user_id",
            "chatter_login",
            "chatter_display_name",
            "message_text",
            "message_fragments",
            "badges",
            "emotes",
            "mentions",
            "reply",
            "raw_event",
            "event_ts",
            "received_at",
            "source",
        ]
        CLICKHOUSE_BATCH_SIZE.observe(len(rows))
        with CLICKHOUSE_INSERT_LATENCY.time():
            try:
                async with self._lock:
                    await asyncio.to_thread(
                        self._client.insert,
                        "chat_messages",
                        rows,
                        column_names=columns,
                    )
                CLICKHOUSE_ROWS_INSERTED.inc(len(rows))
            except Exception:
                CLICKHOUSE_INSERT_ERRORS.inc()
                raise

    async def recent_messages(
        self,
        channel: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[ChatMessage]:
        where, params = self._message_filters(channel=channel, session_id=session_id)
        query = f"""
            SELECT
                message_id,
                eventsub_message_id,
                channel_id,
                channel_login,
                channel_display_name,
                session_id,
                session_date,
                chatter_user_id,
                chatter_login,
                chatter_display_name,
                message_text,
                message_fragments,
                badges,
                emotes,
                mentions,
                reply,
                raw_event,
                event_ts,
                received_at,
                source
            FROM chat_messages
            {where}
            ORDER BY event_ts DESC, message_id
            LIMIT 1 BY message_id
            LIMIT %(limit)s
        """
        params["limit"] = limit
        result = await self._query(query, params)
        messages = [self._message_from_row(row) for row in result.result_rows]
        return list(reversed(messages))

    async def volume_by_minute(
        self,
        channel: str | None = None,
        session_id: str | None = None,
        limit: int = 120,
    ) -> list[VolumePoint]:
        where, params = self._message_filters(channel=channel, session_id=session_id)
        query = f"""
            SELECT
                toStartOfMinute(event_ts) AS bucket,
                uniqExact(message_id) AS message_count,
                uniqExact(chatter_user_id) AS unique_chatter_count
            FROM chat_messages
            {where}
            GROUP BY bucket
            ORDER BY bucket DESC
            LIMIT %(limit)s
        """
        params["limit"] = limit
        result = await self._query(query, params)
        rows = [
            VolumePoint(bucket=self._as_utc(row[0]), message_count=int(row[1]), unique_chatter_count=int(row[2]))
            for row in result.result_rows
        ]
        return list(reversed(rows))

    async def volume_by_minute_for_channels(self, limit: int = 120) -> dict[str, list[VolumePoint]]:
        query = """
            SELECT
                channel_login,
                toStartOfMinute(event_ts) AS bucket,
                uniqExact(message_id) AS message_count,
                uniqExact(chatter_user_id) AS unique_chatter_count
            FROM chat_messages
            WHERE source = 'live'
            GROUP BY channel_login, bucket
            ORDER BY channel_login, bucket DESC
            LIMIT %(limit)s BY channel_login
        """
        result = await self._query(query, {"limit": limit})
        by_channel: dict[str, list[VolumePoint]] = {}
        for row in result.result_rows:
            channel_login = str(row[0])
            by_channel.setdefault(channel_login, []).append(
                VolumePoint(bucket=self._as_utc(row[1]), message_count=int(row[2]), unique_chatter_count=int(row[3]))
            )
        for points in by_channel.values():
            points.reverse()
        return by_channel

    async def message_total(
        self,
        channel: str | None = None,
        session_id: str | None = None,
    ) -> int:
        where, params = self._message_filters(channel=channel, session_id=session_id)
        query = f"""
            SELECT uniqExact(message_id) AS message_count
            FROM chat_messages
            {where}
        """
        result = await self._query(query, params)
        if not result.result_rows:
            return 0
        return int(result.result_rows[0][0])

    async def top_chatters(
        self,
        channel: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[TopItem]:
        where, params = self._message_filters(channel=channel, session_id=session_id)
        query = f"""
            SELECT chatter_login, uniqExact(message_id) AS message_count
            FROM chat_messages
            {where}
            GROUP BY chatter_login
            ORDER BY message_count DESC
            LIMIT %(limit)s
        """
        params["limit"] = limit
        result = await self._query(query, params)
        return [TopItem(value=str(row[0]), count=int(row[1])) for row in result.result_rows]

    async def top_emotes(
        self,
        channel: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[TopItem]:
        where, params = self._message_filters(channel=channel, session_id=session_id)
        query = f"""
            SELECT JSONExtractString(arrayJoin(JSONExtractArrayRaw(emotes)), 'text') AS emote, count() AS emote_count
            FROM (
                SELECT message_id, emotes
                FROM chat_messages
                {where}
                LIMIT 1 BY message_id
            )
            GROUP BY emote
            HAVING emote != ''
            ORDER BY emote_count DESC
            LIMIT %(limit)s
        """
        params["limit"] = limit
        result = await self._query(query, params)
        return [TopItem(value=str(row[0]), count=int(row[1])) for row in result.result_rows]

    async def summary_context(
        self,
        channel: str,
        window_minutes: int,
        max_messages: int,
    ) -> SummaryContext | None:
        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(minutes=window_minutes)
        where, params = self._message_filters(channel=channel, session_id=None)
        time_filter = "event_ts >= %(window_start)s AND event_ts < %(window_end)s"
        where = f"{where} AND {time_filter}" if where else f"WHERE {time_filter}"
        params.update({"window_start": window_start, "window_end": window_end})

        stats_query = f"""
            SELECT
                any(channel_id),
                any(channel_login),
                any(channel_display_name),
                any(session_id),
                uniqExact(message_id),
                uniqExact(chatter_user_id)
            FROM chat_messages
            {where}
        """
        stats_result = await self._query(stats_query, params)
        if not stats_result.result_rows or int(stats_result.result_rows[0][4]) == 0:
            return None

        top_chatters_query = f"""
            SELECT chatter_login, uniqExact(message_id) AS message_count
            FROM chat_messages
            {where}
            GROUP BY chatter_login
            ORDER BY message_count DESC
            LIMIT 10
        """
        top_emotes_query = f"""
            SELECT JSONExtractString(arrayJoin(JSONExtractArrayRaw(emotes)), 'text') AS emote, count() AS emote_count
            FROM (
                SELECT message_id, emotes
                FROM chat_messages
                {where}
                LIMIT 1 BY message_id
            )
            GROUP BY emote
            HAVING emote != ''
            ORDER BY emote_count DESC
            LIMIT 10
        """
        spike_windows_query = f"""
            SELECT
                toStartOfMinute(event_ts) AS bucket,
                uniqExact(message_id) AS message_count,
                uniqExact(chatter_user_id) AS unique_chatter_count
            FROM chat_messages
            {where}
            GROUP BY bucket
            HAVING message_count > 1
            ORDER BY message_count DESC, bucket ASC
            LIMIT 3
        """
        sample_query = f"""
            SELECT
                message_id,
                eventsub_message_id,
                channel_id,
                channel_login,
                channel_display_name,
                session_id,
                session_date,
                chatter_user_id,
                chatter_login,
                chatter_display_name,
                message_text,
                message_fragments,
                badges,
                emotes,
                mentions,
                reply,
                raw_event,
                event_ts,
                received_at,
                source
            FROM chat_messages
            {where}
            AND length(message_text) > 0
            ORDER BY event_ts DESC, message_id
            LIMIT 1 BY message_id
            LIMIT %(max_messages)s
        """
        sample_params = dict(params)
        sample_params["max_messages"] = max_messages

        top_chatters_result = await self._query(top_chatters_query, params)
        top_emotes_result = await self._query(top_emotes_query, params)
        spike_windows_result = await self._query(spike_windows_query, params)
        sample_result = await self._query(sample_query, sample_params)

        stats_row = stats_result.result_rows[0]
        sample_messages = [self._message_from_row(row) for row in sample_result.result_rows]
        sample_messages.reverse()
        return SummaryContext(
            channel_id=str(stats_row[0]),
            channel_login=str(stats_row[1]),
            channel_display_name=str(stats_row[2]),
            session_id=str(stats_row[3]),
            window_start=window_start,
            window_end=window_end,
            window_size=f"{window_minutes}m",
            stats=SummarySourceStats(
                message_count=int(stats_row[4]),
                unique_chatter_count=int(stats_row[5]),
                top_chatters=[TopItem(value=str(row[0]), count=int(row[1])) for row in top_chatters_result.result_rows],
                top_emotes=[TopItem(value=str(row[0]), count=int(row[1])) for row in top_emotes_result.result_rows],
                spike_windows=[
                    SpikeWindow(
                        bucket=self._as_utc(row[0]),
                        message_count=int(row[1]),
                        unique_chatter_count=int(row[2]),
                    )
                    for row in spike_windows_result.result_rows
                ],
                sampled_message_count=len(sample_messages),
            ),
            sample_messages=sample_messages,
        )

    async def insert_summary(self, summary: ChatSummary) -> None:
        columns = [
            "summary_id",
            "channel_id",
            "channel_login",
            "session_id",
            "window_start",
            "window_size",
            "summary_text",
            "model",
            "source_stats",
            "created_at",
        ]
        source_stats = summary.source_stats.model_dump(mode="json")
        source_stats["window_end"] = summary.window_end.isoformat()
        row = (
            summary.summary_id,
            summary.channel_id,
            summary.channel_login,
            summary.session_id,
            summary.window_start,
            summary.window_size,
            summary.summary_text,
            summary.model,
            dumps(source_stats),
            summary.created_at,
        )
        async with self._lock:
            await asyncio.to_thread(self._client.insert, "chat_summaries", [row], column_names=columns)

    async def insert_transcript_segments(self, segments: list[TranscriptSegment]) -> None:
        if not segments:
            return
        columns = [
            "segment_id",
            "channel_login",
            "session_id",
            "segment_started_at",
            "segment_ended_at",
            "audio_path",
            "transcript_text",
            "model",
            "status",
            "error",
            "created_at",
        ]
        rows = [self._transcript_segment_row(segment) for segment in segments]
        async with self._lock:
            await asyncio.to_thread(
                self._client.insert,
                "stream_transcript_segments",
                rows,
                column_names=columns,
            )

    async def ensure_transcript_segments_table(self) -> None:
        query = """
            CREATE TABLE IF NOT EXISTS stream_transcript_segments
            (
                segment_id String,
                channel_login LowCardinality(String),
                session_id String,
                segment_started_at DateTime64(3, 'UTC'),
                segment_ended_at DateTime64(3, 'UTC'),
                audio_path String,
                transcript_text String,
                model LowCardinality(String),
                status LowCardinality(String),
                error String,
                created_at DateTime64(3, 'UTC') DEFAULT now64(3)
            )
            ENGINE = ReplacingMergeTree(created_at)
            PARTITION BY toYYYYMM(segment_started_at)
            ORDER BY (channel_login, session_id, segment_started_at, segment_id)
        """
        async with self._lock:
            await asyncio.to_thread(self._client.command, query)

    async def ensure_vod_schema(self) -> None:
        """Idempotently migrate existing volumes for VOD analysis.

        The init SQL only runs on a fresh ClickHouse volume, so the consumer
        worker (which inserts the ``source`` column) and the API call this at
        startup. Adds ``chat_messages.source``, re-keys the chat_messages TTL on
        ``received_at`` (VOD replays carry the original air time in ``event_ts``)
        and creates ``vod_analyses``.
        """
        add_source = (
            "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS "
            "source LowCardinality(String) DEFAULT 'live' AFTER received_at"
        )
        engine_query = (
            "SELECT engine_full FROM system.tables WHERE database = currentDatabase() AND name = 'chat_messages'"
        )
        modify_ttl = f"ALTER TABLE chat_messages MODIFY TTL {CHAT_MESSAGES_TTL_EXPRESSION}"
        async with self._lock:
            await asyncio.to_thread(self._client.command, add_source)
            engine_full = await asyncio.to_thread(self._client.command, engine_query)
            ttl_migrated = "toDateTime(received_at)" not in str(engine_full)
            if ttl_migrated:
                # Skip rewriting existing parts: for live rows received_at ~= event_ts,
                # so the old per-part TTL info is equivalent; merges pick up the new rule.
                await asyncio.to_thread(
                    self._client.command,
                    modify_ttl,
                    settings={"materialize_ttl_after_modify": 0},
                )
            await asyncio.to_thread(self._client.command, VOD_ANALYSES_DDL)
        logger.info(
            "VOD schema ensured: chat_messages.source present, TTL on received_at (%s), vod_analyses present",
            "migrated now" if ttl_migrated else "already set",
        )

    async def vod_message_count(self, session_id: str) -> int:
        query = """
            SELECT uniqExact(message_id)
            FROM chat_messages
            WHERE session_id = %(session_id)s AND source = 'vod'
        """
        result = await self._query(query, {"session_id": session_id})
        if not result.result_rows:
            return 0
        return int(result.result_rows[0][0])

    async def vod_activity(
        self,
        *,
        session_id: str,
        created_at: datetime,
        duration_seconds: int,
        bucket_seconds: int,
    ) -> list[VodActivityBucket]:
        if duration_seconds <= 0 or bucket_seconds <= 0:
            return []
        query = """
            SELECT
                intDiv(toUnixTimestamp64Milli(event_ts) - %(created_ms)s, %(bucket_ms)s) AS bucket_index,
                uniqExact(message_id) AS message_count,
                uniqExact(chatter_user_id) AS unique_chatter_count
            FROM chat_messages
            WHERE session_id = %(session_id)s AND source = 'vod'
                AND toUnixTimestamp64Milli(event_ts) >= %(created_ms)s
            GROUP BY bucket_index
            ORDER BY bucket_index
        """
        # Integer ms, not datetime params: clickhouse-connect renders datetimes at
        # whole-second precision, which would let pre-start messages into bucket 0.
        params = {
            "session_id": session_id,
            "created_ms": _to_unix_ms(created_at),
            "bucket_ms": int(bucket_seconds) * 1000,
        }
        result = await self._query(query, params)
        return _fill_activity_buckets(result.result_rows, duration_seconds, bucket_seconds)

    async def vod_peak_context(
        self,
        *,
        session_id: str,
        window_start: datetime,
        window_end: datetime,
        top_limit: int = 5,
        token_limit: int = 40,
        sample_limit: int = 20,
    ) -> tuple[int, int, list[TopItem], list[TopItem], list[str]]:
        """Return ``(message_count, unique_chatters, top_emotes, top_tokens, samples)``
        for VOD chat in ``[window_start, window_end)``."""
        where = (
            "WHERE session_id = %(session_id)s AND source = 'vod' "
            "AND toUnixTimestamp64Milli(event_ts) >= %(start_ms)s "
            "AND toUnixTimestamp64Milli(event_ts) < %(end_ms)s"
        )
        params: dict[str, Any] = {
            "session_id": session_id,
            "start_ms": _to_unix_ms(window_start),
            "end_ms": _to_unix_ms(window_end),
        }
        stats_query = f"""
            SELECT uniqExact(message_id), uniqExact(chatter_user_id)
            FROM chat_messages
            {where}
        """
        emotes_query = f"""
            SELECT JSONExtractString(arrayJoin(JSONExtractArrayRaw(emotes)), 'text') AS emote, count() AS emote_count
            FROM (
                SELECT message_id, emotes
                FROM chat_messages
                {where}
                LIMIT 1 BY message_id
            )
            GROUP BY emote
            HAVING emote != ''
            ORDER BY emote_count DESC, emote
            LIMIT %(top_limit)s
        """
        tokens_query = f"""
            SELECT token, count() AS token_count
            FROM (
                SELECT message_id, message_text
                FROM chat_messages
                {where}
                LIMIT 1 BY message_id
            )
            ARRAY JOIN splitByNonAlpha(lowerUTF8(message_text)) AS token
            WHERE length(token) >= 3
            GROUP BY token
            ORDER BY token_count DESC, token
            LIMIT %(token_limit)s
        """
        sample_query = f"""
            SELECT chatter_display_name, chatter_login, message_text
            FROM chat_messages
            {where}
            AND length(message_text) > 0
            ORDER BY cityHash64(message_id), message_id
            LIMIT 1 BY message_id
            LIMIT %(sample_limit)s
        """
        stats_result = await self._query(stats_query, params)
        emotes_result = await self._query(emotes_query, {**params, "top_limit": top_limit})
        tokens_result = await self._query(tokens_query, {**params, "token_limit": token_limit})
        sample_result = await self._query(sample_query, {**params, "sample_limit": sample_limit})

        stats_row = stats_result.result_rows[0] if stats_result.result_rows else (0, 0)
        emotes = [TopItem(value=str(row[0]), count=int(row[1])) for row in emotes_result.result_rows]
        tokens = [TopItem(value=str(row[0]), count=int(row[1])) for row in tokens_result.result_rows]
        samples = [f"{row[0] or row[1]}: {row[2]}" for row in sample_result.result_rows]
        return int(stats_row[0]), int(stats_row[1]), emotes, tokens, samples

    async def upsert_vod_analysis(self, analysis: VodAnalysis) -> None:
        """Insert a full row; callers must bump ``updated_at`` on every write because ReplacingMergeTree dedups on it."""
        # ReplacingMergeTree(updated_at) keeps the newest row per video_id, so a
        # full-row insert is an upsert; updated_at comes from the model.
        row = self._vod_analysis_row(analysis)
        async with self._lock:
            await asyncio.to_thread(
                self._client.insert,
                "vod_analyses",
                [row],
                column_names=list(VOD_ANALYSIS_COLUMNS),
            )

    async def get_vod_analysis(self, video_id: str) -> VodAnalysis | None:
        query = f"""
            SELECT {", ".join(VOD_ANALYSIS_COLUMNS)}
            FROM vod_analyses
            WHERE video_id = %(id)s
            ORDER BY updated_at DESC
            LIMIT 1
        """
        result = await self._query(query, {"id": video_id})
        if not result.result_rows:
            return None
        return self._vod_analysis_from_row(result.result_rows[0])

    async def recent_vod_analyses(self, limit: int = 20) -> list[VodAnalysis]:
        columns = ", ".join(VOD_ANALYSIS_COLUMNS)
        query = f"""
            SELECT {columns}
            FROM (
                SELECT {columns}
                FROM vod_analyses
                ORDER BY updated_at DESC
                LIMIT 1 BY video_id
            )
            ORDER BY analyzed_at DESC
            LIMIT %(limit)s
        """
        result = await self._query(query, {"limit": limit})
        return [self._vod_analysis_from_row(row) for row in result.result_rows]

    async def recent_summaries(
        self,
        channel: str | None = None,
        limit: int = 20,
    ) -> list[ChatSummary]:
        filters: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if channel:
            filters.append("channel_login = %(channel)s")
            params["channel"] = channel.lower()
        where = "WHERE " + " AND ".join(filters) if filters else ""
        query = f"""
            SELECT
                summary_id,
                channel_id,
                channel_login,
                session_id,
                window_start,
                window_size,
                summary_text,
                model,
                source_stats,
                created_at
            FROM chat_summaries
            {where}
            ORDER BY created_at DESC
            LIMIT %(limit)s
        """
        result = await self._query(query, params)
        return [self._summary_from_row(row) for row in result.result_rows]

    async def _query(self, query: str, params: dict[str, Any]):
        async with self._lock:
            return await asyncio.to_thread(self._client.query, query, params)

    def _message_filters(
        self,
        channel: str | None,
        session_id: str | None,
        source: str | None = "live",
    ) -> tuple[str, dict[str, Any]]:
        """Build a WHERE clause for chat_messages.

        ``source`` defaults to ``"live"`` so every dashboard query silently
        excludes replayed VOD chat; pass ``None`` to disable the filter.
        """
        filters: list[str] = []
        params: dict[str, Any] = {}
        if source is not None:
            filters.append("source = %(source)s")
            params["source"] = source
        if channel:
            filters.append("channel_login = %(channel)s")
            params["channel"] = channel.lower()
        if session_id:
            filters.append("session_id = %(session_id)s")
            params["session_id"] = session_id
        if not filters:
            return "", params
        return "WHERE " + " AND ".join(filters), params

    def _message_row(self, message: ChatMessage) -> tuple[Any, ...]:
        return (
            message.message_id,
            message.eventsub_message_id,
            message.channel_id,
            message.channel_login,
            message.channel_display_name,
            message.session_id,
            message.session_date,
            message.chatter_user_id,
            message.chatter_login,
            message.chatter_display_name,
            message.message_text,
            dumps(message.message_fragments),
            dumps(message.badges),
            dumps(message.emotes),
            dumps(message.mentions),
            dumps(message.reply or {}),
            dumps(message.raw_event),
            message.event_ts,
            message.received_at,
            message.source,
        )

    def _message_from_row(self, row: tuple[Any, ...]) -> ChatMessage:
        return ChatMessage(
            message_id=row[0],
            eventsub_message_id=row[1],
            channel_id=row[2],
            channel_login=row[3],
            channel_display_name=row[4],
            session_id=row[5],
            session_date=row[6],
            chatter_user_id=row[7],
            chatter_login=row[8],
            chatter_display_name=row[9],
            message_text=row[10],
            message_fragments=loads(row[11]),
            badges=loads(row[12]),
            emotes=loads(row[13]),
            mentions=loads(row[14]),
            reply=loads(row[15]) or None,
            raw_event=loads(row[16]),
            event_ts=self._as_utc(row[17]),
            received_at=self._as_utc(row[18]),
            source=str(row[19]) if len(row) > 19 and row[19] else "live",
        )

    def _summary_from_row(self, row: tuple[Any, ...]) -> ChatSummary:
        source_stats = loads(row[8])
        window_end = self._window_end(self._as_utc(row[4]), str(row[5]), source_stats)
        stats = SummarySourceStats(
            message_count=int(source_stats.get("message_count", 0)),
            unique_chatter_count=int(source_stats.get("unique_chatter_count", 0)),
            top_chatters=[TopItem(**item) for item in source_stats.get("top_chatters", [])],
            top_emotes=[TopItem(**item) for item in source_stats.get("top_emotes", [])],
            spike_windows=[SpikeWindow(**item) for item in source_stats.get("spike_windows", [])],
            sampled_message_count=int(source_stats.get("sampled_message_count", 0)),
        )
        return ChatSummary(
            summary_id=row[0],
            channel_id=row[1],
            channel_login=row[2],
            session_id=row[3],
            window_start=self._as_utc(row[4]),
            window_end=window_end,
            window_size=row[5],
            summary_text=row[6],
            model=row[7],
            source_stats=stats,
            created_at=self._as_utc(row[9]),
        )

    def _transcript_segment_row(self, segment: TranscriptSegment) -> tuple[Any, ...]:
        return (
            segment.segment_id,
            segment.channel_login,
            segment.session_id,
            segment.segment_started_at,
            segment.segment_ended_at,
            segment.audio_path,
            segment.transcript_text,
            segment.model,
            segment.status,
            segment.error,
            segment.created_at,
        )

    def _vod_analysis_row(self, analysis: VodAnalysis) -> tuple[Any, ...]:
        """Row in ``VOD_ANALYSIS_COLUMNS`` order; peaks are stored as a JSON array."""
        return (
            analysis.video_id,
            analysis.channel_id,
            analysis.channel_login,
            analysis.channel_display_name,
            analysis.title,
            analysis.video_created_at,
            max(0, int(analysis.duration_seconds)),
            max(0, int(analysis.bucket_seconds)),
            max(0, int(analysis.message_count)),
            max(0, int(analysis.unique_chatter_count)),
            analysis.status,
            analysis.error,
            dumps([peak.model_dump(mode="json") for peak in analysis.peaks]),
            analysis.label_model,
            analysis.analyzed_at,
            analysis.updated_at,
        )

    def _vod_analysis_from_row(self, row: Sequence[Any]) -> VodAnalysis:
        raw_peaks = loads(row[12]) if row[12] else []
        return VodAnalysis(
            video_id=str(row[0]),
            channel_id=str(row[1]),
            channel_login=str(row[2]),
            channel_display_name=str(row[3]),
            title=str(row[4]),
            video_created_at=self._as_utc(row[5]),
            duration_seconds=int(row[6]),
            bucket_seconds=int(row[7]),
            message_count=int(row[8]),
            unique_chatter_count=int(row[9]),
            status=str(row[10]),
            error=str(row[11]),
            peaks=[VodPeak.model_validate(peak) for peak in raw_peaks],
            label_model=str(row[13] or ""),
            analyzed_at=self._as_utc(row[14]),
            updated_at=self._as_utc(row[15]),
        )

    def _window_end(self, window_start: datetime, window_size: str, source_stats: dict[str, Any]) -> datetime:
        if source_stats.get("window_end"):
            return datetime.fromisoformat(source_stats["window_end"]).astimezone(UTC)
        if window_size.endswith("m"):
            return window_start + timedelta(minutes=int(window_size[:-1]))
        return window_start

    def _as_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _connect_with_retry(self, attempts: int = 30, delay_seconds: float = 2.0, **kwargs: Any):
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                return clickhouse_connect.get_client(**kwargs)
            except Exception as exc:
                last_error = exc
                time.sleep(delay_seconds)
        raise RuntimeError("ClickHouse is not reachable") from last_error
