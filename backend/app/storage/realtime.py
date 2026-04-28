import asyncio
from collections import deque
from contextlib import suppress

from fastapi import WebSocket

from app.core.json import dumps
from app.core.metrics import WEBSOCKET_CLIENTS
from app.models.chat import ChatMessage, LiveEnvelope


class RealtimeHub:
    def __init__(self, recent_limit: int) -> None:
        self._connections: set[WebSocket] = set()
        self._recent: deque[ChatMessage] = deque(maxlen=recent_limit)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
            WEBSOCKET_CLIENTS.set(len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
            WEBSOCKET_CLIENTS.set(len(self._connections))

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
            connections = list(self._connections)

        stale: list[WebSocket] = []
        for websocket in connections:
            try:
                await websocket.send_text(data)
            except Exception:
                stale.append(websocket)

        if stale:
            async with self._lock:
                for websocket in stale:
                    self._connections.discard(websocket)
                    with suppress(Exception):
                        await websocket.close()
                WEBSOCKET_CLIENTS.set(len(self._connections))
