import asyncio
import logging
import signal
from contextlib import suppress
from typing import Any

from aiokafka import AIOKafkaConsumer
from prometheus_client import start_http_server

from app.core.config import get_settings
from app.core.json import loads
from app.core.metrics import WORKER_BATCHES_INSERTED, WORKER_KAFKA_CONNECTED
from app.models.chat import ChatMessage
from app.storage.clickhouse import ClickHouseRepository

logger = logging.getLogger(__name__)


def _record_location(record: Any) -> str:
    return (
        f"{getattr(record, 'topic', '<unknown>')}:"
        f"{getattr(record, 'partition', '<unknown>')}:"
        f"{getattr(record, 'offset', '<unknown>')}"
    )


def chat_message_from_record(record: Any) -> ChatMessage | None:
    try:
        return ChatMessage.model_validate(loads(record.value))
    except Exception:
        logger.exception("Skipping invalid Kafka chat message at %s", _record_location(record))
        return None


class ClickHouseConsumerWorker:
    def __init__(self, batch_size: int = 1000, flush_interval_seconds: float = 1.0) -> None:
        settings = get_settings()
        self._settings = settings
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_seconds
        self._stop = asyncio.Event()
        self._consumer = AIOKafkaConsumer(
            settings.kafka_chat_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        self._repo: ClickHouseRepository | None = None

    async def run(self) -> None:
        settings = self._settings
        self._repo = await ClickHouseRepository.connect(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_username,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
        )
        await self._start_consumer_with_retry()
        WORKER_KAFKA_CONNECTED.set(1)
        logger.info("ClickHouse consumer started")
        batch: list[ChatMessage] = []
        retry_pending = False
        last_flush = asyncio.get_running_loop().time()

        try:
            while not self._stop.is_set():
                consumed_records = False
                if retry_pending:
                    # A batch is awaiting an insert retry: do not fetch more records so
                    # the in-memory batch stays bounded while ClickHouse is unavailable.
                    await asyncio.sleep(self._flush_interval_seconds)
                else:
                    records = await self._consumer.getmany(timeout_ms=500, max_records=self._batch_size)
                    for partition_records in records.values():
                        for record in partition_records:
                            consumed_records = True
                            message = chat_message_from_record(record)
                            if message is not None:
                                batch.append(message)

                now = asyncio.get_running_loop().time()
                should_flush = batch and (
                    retry_pending
                    or len(batch) >= self._batch_size
                    or now - last_flush >= self._flush_interval_seconds
                )
                if should_flush:
                    try:
                        await self._repo.insert_messages(batch)
                    except Exception:
                        retry_pending = True
                        logger.exception("Failed to insert ClickHouse batch; will retry without committing offsets")
                    else:
                        retry_pending = False
                        WORKER_BATCHES_INSERTED.inc()
                        await self._commit_offsets()
                        logger.info("Inserted %s chat messages into ClickHouse", len(batch))
                        batch.clear()
                        last_flush = now
                elif consumed_records and not batch:
                    await self._commit_offsets()
        finally:
            try:
                if batch:
                    try:
                        await self._repo.insert_messages(batch)
                    except Exception:
                        logger.exception("Failed to insert final ClickHouse batch; leaving offsets uncommitted")
                    else:
                        WORKER_BATCHES_INSERTED.inc()
                        await self._commit_offsets()
            finally:
                try:
                    await self._consumer.stop()
                finally:
                    WORKER_KAFKA_CONNECTED.set(0)

    async def _commit_offsets(self) -> None:
        # Commit failures (e.g. CommitFailedError during a rebalance) must not crash the
        # worker: the uncommitted records are simply replayed, and replay is safe because
        # chat_messages is a ReplacingMergeTree keyed for dedup.
        try:
            await self._consumer.commit()
        except Exception:
            logger.exception("Kafka offset commit failed; continuing (replay is deduplicated by ClickHouse)")

    def stop(self) -> None:
        self._stop.set()

    async def _start_consumer_with_retry(self, attempts: int = 30, delay_seconds: float = 2.0) -> None:
        for attempt in range(1, attempts + 1):
            try:
                await self._consumer.start()
                return
            except Exception:
                with suppress(Exception):
                    await self._consumer.stop()
                if attempt == attempts:
                    raise
                logger.warning("Kafka is not ready yet; retrying consumer start (%s/%s)", attempt, attempts)
                await asyncio.sleep(delay_seconds)


async def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    start_http_server(9101)
    worker = ClickHouseConsumerWorker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
