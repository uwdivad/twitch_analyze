import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.models.chat import (
    ChannelInfo,
    ChatMessage,
    ChatSummary,
    GenerateSummaryRequest,
    InsertResult,
    MessageTotal,
    SummaryContext,
    TopItem,
    StartTranscriptionRequest,
    TranscriptionJob,
    VolumePoint,
)
from app.services.summaries import SummaryService
from app.storage.clickhouse import ClickHouseRepository
from app.storage.realtime import RealtimeHub
from app.workers.audio_capture import AudioCaptureWorker

router = APIRouter()
logger = logging.getLogger(__name__)


def get_clickhouse(request: Request) -> ClickHouseRepository:
    return request.app.state.clickhouse


def get_hub(request: Request) -> RealtimeHub:
    return request.app.state.realtime_hub


def get_summary_service(
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
) -> SummaryService:
    return SummaryService(
        clickhouse=clickhouse,
        api_key=settings.openai_api_key,
        model=settings.openai_summary_model,
        max_messages=settings.openai_summary_max_messages,
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/channels", response_model=list[ChannelInfo])
async def channels(request: Request) -> list[ChannelInfo]:
    return list(request.app.state.channels.values())


@router.post("/api/transcriptions/start", response_model=TranscriptionJob)
async def start_transcription(request: Request, payload: StartTranscriptionRequest) -> TranscriptionJob:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is required for transcription")

    channel_login = payload.channel.lower()
    duration_seconds = payload.duration_minutes * 60
    started_at = datetime.now(UTC)
    job = TranscriptionJob(
        job_id=str(uuid.uuid4()),
        channel_login=channel_login,
        duration_seconds=duration_seconds,
        status="running",
        started_at=started_at,
        ends_at=started_at + timedelta(seconds=duration_seconds),
    )
    request.app.state.transcription_jobs[job.job_id] = job

    async def run_job() -> None:
        worker = AudioCaptureWorker()
        try:
            processed = await worker.capture_channel_for_duration(channel_login, duration_seconds)
        except Exception as exc:
            logger.exception("Timed transcription job failed")
            request.app.state.transcription_jobs[job.job_id] = job.model_copy(
                update={"status": "failed", "detail": str(exc)}
            )
        else:
            request.app.state.transcription_jobs[job.job_id] = job.model_copy(
                update={"status": "completed", "detail": f"Transcribed {processed} audio chunks"}
            )

    task = asyncio.create_task(run_job())
    request.app.state.transcription_tasks[job.job_id] = task
    task.add_done_callback(lambda _: request.app.state.transcription_tasks.pop(job.job_id, None))
    return job


@router.get("/api/transcriptions/jobs/{job_id}", response_model=TranscriptionJob)
async def transcription_job(request: Request, job_id: str) -> TranscriptionJob:
    job = request.app.state.transcription_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Transcription job not found")
    return job


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


@router.post("/api/messages", response_model=InsertResult)
async def insert_messages(
    messages: list[ChatMessage],
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> InsertResult:
    try:
        await clickhouse.insert_messages(messages)
        return InsertResult(inserted=len(messages))
    except Exception as exc:
        logger.exception("Failed to insert messages into ClickHouse")
        raise HTTPException(status_code=500, detail="Failed to insert messages") from exc


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


@router.get("/api/analytics/volume-by-channel", response_model=dict[str, list[VolumePoint]])
async def volume_by_channel(
    limit: int = Query(default=120, ge=1, le=1440),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> dict[str, list[VolumePoint]]:
    try:
        return await clickhouse.volume_by_minute_for_channels(limit=limit)
    except Exception:
        logger.exception("Failed to load per-channel volume analytics from ClickHouse")
        return {}


@router.get("/api/analytics/message-total", response_model=MessageTotal)
async def message_total(
    channel: str | None = None,
    session_id: str | None = None,
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> MessageTotal:
    try:
        return MessageTotal(count=await clickhouse.message_total(channel=channel, session_id=session_id))
    except Exception:
        logger.exception("Failed to load message total from ClickHouse")
        return MessageTotal(count=0)


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


@router.get("/api/summaries/context", response_model=SummaryContext)
async def summary_context(
    channel: str,
    window_minutes: int = Query(default=60, ge=1, le=1440),
    max_messages: int = Query(default=100, ge=1, le=1000),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> SummaryContext:
    try:
        context = await clickhouse.summary_context(
            channel=channel,
            window_minutes=window_minutes,
            max_messages=max_messages,
        )
    except Exception as exc:
        logger.exception("Failed to load summary context from ClickHouse")
        raise HTTPException(status_code=500, detail="Failed to load summary context") from exc
    if context is None:
        raise HTTPException(status_code=404, detail="No chat messages found for the requested summary window")
    return context


@router.get("/api/summaries", response_model=list[ChatSummary])
async def summaries(
    channel: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> list[ChatSummary]:
    try:
        return await clickhouse.recent_summaries(channel=channel, limit=limit)
    except Exception:
        logger.exception("Failed to load chat summaries from ClickHouse")
        return []


@router.post("/api/summaries", response_model=InsertResult)
async def insert_summary(
    summary: ChatSummary,
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> InsertResult:
    try:
        await clickhouse.insert_summary(summary)
        return InsertResult(inserted=1)
    except Exception as exc:
        logger.exception("Failed to insert chat summary into ClickHouse")
        raise HTTPException(status_code=500, detail="Failed to insert chat summary") from exc


@router.post("/api/summaries/generate", response_model=ChatSummary)
async def generate_summary(
    request: GenerateSummaryRequest,
    service: SummaryService = Depends(get_summary_service),
) -> ChatSummary:
    try:
        return await service.generate(channel=request.channel, window_minutes=request.window_minutes)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to generate chat summary")
        raise HTTPException(status_code=500, detail="Failed to generate chat summary") from exc


@router.get("/api/messages/stream")
async def message_stream(request: Request, hub: RealtimeHub = Depends(get_hub)) -> StreamingResponse:
    queue = await hub.subscribe()

    async def event_source():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            await hub.unsubscribe(queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
