import asyncio
from collections import deque

from app.core.json import dumps
from app.core.metrics import SSE_CLIENTS
from app.models.chat import ChatMessage, LiveEnvelope

# Bound per-subscriber queues so one slow SSE client can't grow memory without limit;
# it gets dropped instead of stalling broadcasts to everyone else.
_SUBSCRIBER_QUEUE_SIZE = 1000


class RealtimeHub:
    def __init__(self, recent_limit: int) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._recent: deque[ChatMessage] = deque(maxlen=recent_limit)
        self._lock = asyncio.Lock()

    async def subscribe(self) -> "asyncio.Queue[str]":
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(queue)
            SSE_CLIENTS.set(len(self._subscribers))
        return queue

    async def unsubscribe(self, queue: "asyncio.Queue[str]") -> None:
        async with self._lock:
            self._subscribers.discard(queue)
            SSE_CLIENTS.set(len(self._subscribers))

    def recent(self, channel: str | None = None, limit: int = 100) -> list[ChatMessage]:
        messages = list(self._recent)
        if channel:
            channel_lower = channel.lower()
            messages = [msg for msg in messages if msg.channel_login.lower() == channel_lower]
        return messages[-limit:]

    async def publish_message(self, message: ChatMessage) -> None:
        self._recent.append(message)
        await self.broadcast(LiveEnvelope(type="chat_message", payload=message))

    async def broadcast_status(self, payload: dict) -> None:
        await self.broadcast(LiveEnvelope(type="status", payload=payload))

    async def broadcast(self, envelope: LiveEnvelope) -> None:
        data = dumps(envelope.model_dump(mode="json"))
        async with self._lock:
            subscribers = list(self._subscribers)

        stale: list[asyncio.Queue[str]] = []
        for queue in subscribers:
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                stale.append(queue)

        if stale:
            async with self._lock:
                for queue in stale:
                    self._subscribers.discard(queue)
                SSE_CLIENTS.set(len(self._subscribers))
