import asyncio
import logging

from aiokafka import AIOKafkaProducer

from app.core.json import dumps
from app.core.metrics import KAFKA_MESSAGES_PUBLISHED, KAFKA_PUBLISH_ERRORS, KAFKA_PUBLISH_LATENCY
from pydantic import BaseModel

from app.models.chat import ChatMessage

logger = logging.getLogger(__name__)


class KafkaJsonProducer:
    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._producer: AIOKafkaProducer | None = None

    async def start(self, attempts: int = 30, delay_seconds: float = 2.0) -> None:
        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda value: dumps(value).encode("utf-8"),
                key_serializer=lambda value: value.encode("utf-8"),
                acks="all",
            )
            for attempt in range(1, attempts + 1):
                try:
                    await self._producer.start()
                    return
                except Exception:
                    if attempt == attempts:
                        raise
                    logger.warning("Kafka is not ready yet; retrying producer start (%s/%s)", attempt, attempts)
                    await asyncio.sleep(delay_seconds)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def flush(self) -> None:
        """Wait until every queued message has been delivered (or failed).

        Used by bulk producers (e.g. VOD replay) that enqueue without awaiting
        per-message acks. No-op when the producer is not started.
        """
        if self._producer is not None:
            await self._producer.flush()

    @staticmethod
    def _message_key(message: BaseModel) -> str:
        channel = getattr(message, "channel_id", "") or getattr(message, "channel_login", "")
        session_id = getattr(message, "session_id", "")
        return f"{channel}:{session_id}"

    async def publish(self, message: BaseModel) -> None:
        if self._producer is None:
            raise RuntimeError("Kafka producer is not started")

        key = self._message_key(message)
        with KAFKA_PUBLISH_LATENCY.labels(topic=self._topic).time():
            try:
                await self._producer.send_and_wait(
                    self._topic,
                    key=key,
                    value=message.model_dump(mode="json"),
                )
                KAFKA_MESSAGES_PUBLISHED.labels(topic=self._topic).inc()
            except Exception:
                KAFKA_PUBLISH_ERRORS.labels(topic=self._topic).inc()
                raise

    def _on_delivery(self, future) -> None:  # noqa: ANN001 - asyncio.Future from aiokafka
        if future.cancelled():
            KAFKA_PUBLISH_ERRORS.labels(topic=self._topic).inc()
            logger.error("Kafka delivery cancelled for topic %s", self._topic)
            return
        exc = future.exception()
        if exc is not None:
            KAFKA_PUBLISH_ERRORS.labels(topic=self._topic).inc()
            logger.error("Kafka delivery failed for topic %s: %s", self._topic, exc)
        else:
            KAFKA_MESSAGES_PUBLISHED.labels(topic=self._topic).inc()


class KafkaChatProducer(KafkaJsonProducer):
    async def publish(self, message: ChatMessage) -> None:
        """Enqueue a chat message without awaiting broker acks (hot path).

        aiokafka batches queued messages internally and still requests acks="all";
        delivery failures are logged via the future callback instead of blocking
        per-message. Flush-on-shutdown is preserved because AIOKafkaProducer.stop()
        drains the queue before returning.
        """
        if self._producer is None:
            raise RuntimeError("Kafka producer is not started")

        key = self._message_key(message)
        with KAFKA_PUBLISH_LATENCY.labels(topic=self._topic).time():
            try:
                delivery_future = await self._producer.send(
                    self._topic,
                    key=key,
                    value=message.model_dump(mode="json"),
                )
            except Exception:
                KAFKA_PUBLISH_ERRORS.labels(topic=self._topic).inc()
                raise
        delivery_future.add_done_callback(self._on_delivery)
