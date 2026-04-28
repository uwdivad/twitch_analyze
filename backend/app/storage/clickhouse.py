import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect

from app.core.json import dumps, loads
from app.core.metrics import (
    CLICKHOUSE_BATCH_SIZE,
    CLICKHOUSE_INSERT_ERRORS,
    CLICKHOUSE_INSERT_LATENCY,
    CLICKHOUSE_ROWS_INSERTED,
)
from app.models.chat import ChatMessage, TopItem, VolumePoint


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
                received_at
            FROM chat_messages
            {where}
            ORDER BY event_ts DESC
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
                count() AS message_count,
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

    async def top_chatters(
        self,
        channel: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[TopItem]:
        where, params = self._message_filters(channel=channel, session_id=session_id)
        query = f"""
            SELECT chatter_login, count() AS message_count
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
            FROM chat_messages
            {where}
            GROUP BY emote
            HAVING emote != ''
            ORDER BY emote_count DESC
            LIMIT %(limit)s
        """
        params["limit"] = limit
        result = await self._query(query, params)
        return [TopItem(value=str(row[0]), count=int(row[1])) for row in result.result_rows]

    async def _query(self, query: str, params: dict[str, Any]):
        async with self._lock:
            return await asyncio.to_thread(self._client.query, query, params)

    def _message_filters(self, channel: str | None, session_id: str | None) -> tuple[str, dict[str, Any]]:
        filters: list[str] = []
        params: dict[str, Any] = {}
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
        )

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
