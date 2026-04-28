import asyncio
import logging
import signal
from contextlib import suppress

from aiokafka import AIOKafkaConsumer
from prometheus_client import start_http_server

from app.core.config import get_settings
from app.core.json import loads
from app.core.metrics import WORKER_BATCHES_INSERTED, WORKER_KAFKA_CONNECTED
from app.models.chat import ChatMessage
from app.storage.clickhouse import ClickHouseRepository

logger = logging.getLogger(__name__)


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
        self._repo = ClickHouseRepository(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_username,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
        )

    async def run(self) -> None:
        await self._start_consumer_with_retry()
        WORKER_KAFKA_CONNECTED.set(1)
        logger.info("ClickHouse consumer started")
        batch: list[ChatMessage] = []
        last_flush = asyncio.get_running_loop().time()

        try:
            while not self._stop.is_set():
                records = await self._consumer.getmany(timeout_ms=500, max_records=self._batch_size)
                for partition_records in records.values():
                    for record in partition_records:
                        batch.append(ChatMessage(**loads(record.value)))

                now = asyncio.get_running_loop().time()
                should_flush = batch and (
                    len(batch) >= self._batch_size or now - last_flush >= self._flush_interval_seconds
                )
                if should_flush:
                    await self._repo.insert_messages(batch)
                    WORKER_BATCHES_INSERTED.inc()
                    await self._consumer.commit()
                    logger.info("Inserted %s chat messages into ClickHouse", len(batch))
                    batch.clear()
                    last_flush = now
        finally:
            if batch:
                await self._repo.insert_messages(batch)
                WORKER_BATCHES_INSERTED.inc()
                await self._consumer.commit()
            await self._consumer.stop()
            WORKER_KAFKA_CONNECTED.set(0)

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
