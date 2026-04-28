import asyncio
import logging

from aiokafka import AIOKafkaProducer

from app.core.json import dumps
from app.core.metrics import KAFKA_MESSAGES_PUBLISHED, KAFKA_PUBLISH_ERRORS, KAFKA_PUBLISH_LATENCY
from app.models.chat import ChatMessage

logger = logging.getLogger(__name__)


class KafkaChatProducer:
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

    async def publish(self, message: ChatMessage) -> None:
        if self._producer is None:
            raise RuntimeError("Kafka producer is not started")

        key = f"{message.channel_id}:{message.session_id}"
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
