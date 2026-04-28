import logging

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect

from app.models.chat import ChannelInfo, ChatMessage, TopItem, VolumePoint
from app.storage.clickhouse import ClickHouseRepository
from app.storage.realtime import RealtimeHub

router = APIRouter()
logger = logging.getLogger(__name__)


def get_clickhouse(request: Request) -> ClickHouseRepository:
    return request.app.state.clickhouse


def get_hub(request: Request) -> RealtimeHub:
    return request.app.state.realtime_hub


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/channels", response_model=list[ChannelInfo])
async def channels(request: Request) -> list[ChannelInfo]:
    return list(request.app.state.channels.values())


@router.get("/api/messages/recent", response_model=list[ChatMessage])
async def recent_messages(
    channel: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
    hub: RealtimeHub = Depends(get_hub),
) -> list[ChatMessage]:
    try:
        messages = await clickhouse.recent_messages(channel=channel, session_id=session_id, limit=limit)
        if messages:
            return messages
    except Exception:
        logger.exception("Failed to load recent messages from ClickHouse")
    return hub.recent(channel=channel, limit=limit)


@router.get("/api/analytics/volume", response_model=list[VolumePoint])
async def volume(
    channel: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=120, ge=1, le=1440),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> list[VolumePoint]:
    try:
        return await clickhouse.volume_by_minute(channel=channel, session_id=session_id, limit=limit)
    except Exception:
        logger.exception("Failed to load volume analytics from ClickHouse")
        return []


@router.get("/api/analytics/top-chatters", response_model=list[TopItem])
async def top_chatters(
    channel: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> list[TopItem]:
    try:
        return await clickhouse.top_chatters(channel=channel, session_id=session_id, limit=limit)
    except Exception:
        logger.exception("Failed to load top chatters from ClickHouse")
        return []


@router.get("/api/analytics/top-emotes", response_model=list[TopItem])
async def top_emotes(
    channel: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> list[TopItem]:
    try:
        return await clickhouse.top_emotes(channel=channel, session_id=session_id, limit=limit)
    except Exception:
        logger.exception("Failed to load top emotes from ClickHouse")
        return []


@router.websocket("/ws/messages")
async def message_socket(websocket: WebSocket) -> None:
    hub: RealtimeHub = websocket.app.state.realtime_hub
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
