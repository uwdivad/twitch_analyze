import asyncio
import logging
import math
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
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
from app.models.vod import (
    ACTIVE_VOD_STATUSES,
    AnalyzeVodRequest,
    VodActivity,
    VodAnalysis,
    VodAnalysisJob,
    VodAnalysisResponse,
)
from app.services.summaries import SummaryService
from app.services.vod_analysis import VodAnalysisService, VodLabelService, vod_session_id
from app.storage.clickhouse import ClickHouseRepository
from app.storage.realtime import RealtimeHub
from app.workers.audio_capture import AudioCaptureWorker

router = APIRouter()
logger = logging.getLogger(__name__)

# Maximum number of ChatMessage entries accepted in a single POST /api/messages call.
MAX_MESSAGE_BATCH_SIZE = 500
# Maximum number of finished (non-running) transcription jobs kept in memory.
MAX_FINISHED_TRANSCRIPTION_JOBS = 50
# Maximum number of finished VOD analysis jobs kept in memory.
MAX_FINISHED_VOD_JOBS = 50
# Upper bound on buckets returned by the VOD activity endpoint when a caller picks a small bucket.
MAX_VOD_ACTIVITY_BUCKETS = 3600


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Optional API auth: enforced only when API_AUTH_TOKEN is configured."""
    token = settings.api_auth_token
    if not token:
        return
    provided = x_api_key or ""
    if not secrets.compare_digest(provided.encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _prune_finished_jobs(
    jobs: dict[str, TranscriptionJob] | dict[str, VodAnalysisJob],
    max_finished: int = MAX_FINISHED_TRANSCRIPTION_JOBS,
    active_statuses: frozenset[str] = frozenset({"running"}),
) -> None:
    """Keep at most `max_finished` finished jobs, evicting the oldest first."""
    finished = [job_id for job_id, job in jobs.items() if job.status not in active_statuses]
    excess = len(finished) - max_finished
    if excess <= 0:
        return
    for job_id in finished[:excess]:
        jobs.pop(job_id, None)


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
        timeout_seconds=settings.openai_timeout_seconds,
    )


def _default_parse_vod_reference(value: str) -> str:
    # TODO(integration): WS2 provides parse_vod_reference in app.ingestion.vod_replay.
    from app.ingestion.vod_replay import parse_vod_reference

    return parse_vod_reference(value)


def get_vod_reference_parser() -> Callable[[str], str]:
    return _default_parse_vod_reference


def _gql_client_factory(settings: Settings) -> Callable[[], Any]:
    def factory() -> Any:
        # TODO(integration): wire real TwitchGqlClient factory + app.state.kafka.
        # Imported lazily because WS2's module is not on this branch yet.
        from app.ingestion.vod_replay import TwitchGqlClient

        return TwitchGqlClient(
            url=settings.twitch_gql_url,
            client_id=settings.twitch_gql_client_id,
            comments_query_hash=settings.twitch_gql_comments_query_hash,
            max_retries=settings.vod_fetch_max_retries,
            page_delay_seconds=settings.vod_fetch_page_delay_seconds,
        )

    return factory


def get_vod_service(
    request: Request,
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
) -> VodAnalysisService:
    # TODO(integration): wire real TwitchGqlClient factory + app.state.kafka
    # (app.state.kafka must expose flush(), added by WS1).
    return VodAnalysisService(
        clickhouse=clickhouse,
        kafka=request.app.state.kafka,
        gql_factory=_gql_client_factory(settings),
        settings=settings,
        jobs=request.app.state.vod_jobs,
    )


def get_vod_label_service(
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
) -> VodLabelService:
    return VodLabelService(
        clickhouse=clickhouse,
        api_key=settings.openai_api_key,
        model=settings.openai_vod_label_model or settings.openai_summary_model,
        timeout_seconds=settings.openai_timeout_seconds,
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/channels", response_model=list[ChannelInfo])
async def channels(request: Request) -> list[ChannelInfo]:
    return list(request.app.state.channels.values())


@router.post(
    "/api/transcriptions/start",
    response_model=TranscriptionJob,
    dependencies=[Depends(require_api_key)],
)
async def start_transcription(request: Request, payload: StartTranscriptionRequest) -> TranscriptionJob:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is required for transcription")

    running_jobs = len(request.app.state.transcription_tasks)
    if running_jobs >= settings.transcription_max_concurrent_jobs:
        raise HTTPException(status_code=429, detail="Too many concurrent transcription jobs")

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
            # Full exception detail (paths, stderr, traceback) stays in server logs only;
            # the job record exposed via the API gets a sanitized generic message.
            logger.exception("Timed transcription job failed")
            request.app.state.transcription_jobs[job.job_id] = job.model_copy(
                update={"status": "failed", "detail": f"Transcription failed ({type(exc).__name__})"}
            )
        else:
            request.app.state.transcription_jobs[job.job_id] = job.model_copy(
                update={"status": "completed", "detail": f"Transcribed {processed} audio chunks"}
            )
        finally:
            _prune_finished_jobs(request.app.state.transcription_jobs)

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


# NOTE: /api/vods/analyze must be declared before /api/vods/{video_id} routes.
@router.post(
    "/api/vods/analyze",
    response_model=VodAnalysisResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def analyze_vod(
    request: Request,
    payload: AnalyzeVodRequest,
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
    service: VodAnalysisService = Depends(get_vod_service),
    parse_reference: Callable[[str], str] = Depends(get_vod_reference_parser),
) -> VodAnalysisResponse:
    try:
        video_id = parse_reference(payload.video)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Twitch VOD URL or video id") from exc

    jobs: dict[str, VodAnalysisJob] = request.app.state.vod_jobs
    tasks: dict[str, asyncio.Task] = request.app.state.vod_tasks

    existing_job = jobs.get(video_id)
    if existing_job is not None and existing_job.status in ACTIVE_VOD_STATUSES:
        return VodAnalysisResponse(job=existing_job)

    active = sum(1 for job in jobs.values() if job.status in ACTIVE_VOD_STATUSES)
    if active >= settings.vod_max_concurrent_jobs:
        raise HTTPException(status_code=429, detail="Too many concurrent VOD analysis jobs")

    session_id = vod_session_id(video_id)
    if not payload.force:
        try:
            analysis = await clickhouse.get_vod_analysis(video_id)
        except Exception:
            logger.exception("Failed to load existing VOD analysis for %s", video_id)
            analysis = None
        if analysis is not None and analysis.status == "completed":
            return VodAnalysisResponse(analysis=analysis)

    skip_fetch = False
    if not payload.force:
        try:
            skip_fetch = await clickhouse.vod_message_count(session_id) > 0
        except Exception:
            logger.exception("Failed to count stored VOD messages for %s", video_id)
            skip_fetch = False

    now = datetime.now(UTC)
    job = VodAnalysisJob(
        video_id=video_id,
        status="queued",
        started_at=now,
        updated_at=now,
        detail="Using stored chat replay" if skip_fetch else "Queued",
    )
    jobs[video_id] = job

    task = asyncio.create_task(service.run(video_id, skip_fetch=skip_fetch))
    tasks[video_id] = task

    def _on_done(finished: asyncio.Task) -> None:
        if tasks.get(video_id) is finished:
            tasks.pop(video_id, None)
        _prune_finished_jobs(jobs, MAX_FINISHED_VOD_JOBS, active_statuses=ACTIVE_VOD_STATUSES)

    task.add_done_callback(_on_done)
    return VodAnalysisResponse(job=job)


@router.get("/api/vods", response_model=list[VodAnalysis])
async def vod_analyses(
    limit: int = Query(default=20, ge=1, le=100),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> list[VodAnalysis]:
    try:
        return await clickhouse.recent_vod_analyses(limit=limit)
    except Exception:
        logger.exception("Failed to load VOD analyses from ClickHouse")
        return []


@router.get("/api/vods/{video_id}", response_model=VodAnalysisResponse)
async def vod_analysis(
    request: Request,
    video_id: str,
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> VodAnalysisResponse:
    job = request.app.state.vod_jobs.get(video_id)
    try:
        analysis = await clickhouse.get_vod_analysis(video_id)
    except Exception:
        logger.exception("Failed to load VOD analysis %s from ClickHouse", video_id)
        analysis = None
    if job is None and analysis is None:
        raise HTTPException(status_code=404, detail="VOD analysis not found")
    return VodAnalysisResponse(job=job, analysis=analysis)


@router.get("/api/vods/{video_id}/activity", response_model=VodActivity)
async def vod_activity(
    video_id: str,
    bucket_seconds: int | None = Query(default=None, ge=1, le=3600),
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> VodActivity:
    try:
        analysis = await clickhouse.get_vod_analysis(video_id)
    except Exception:
        logger.exception("Failed to load VOD analysis %s from ClickHouse", video_id)
        analysis = None
    if analysis is None:
        raise HTTPException(status_code=404, detail="VOD analysis not found")

    duration = max(analysis.duration_seconds, 0)
    bucket = bucket_seconds or analysis.bucket_seconds or 60
    # Keep caller-chosen buckets from producing an unbounded response.
    bucket = max(bucket, math.ceil(duration / MAX_VOD_ACTIVITY_BUCKETS), 1)
    try:
        buckets = await clickhouse.vod_activity(
            session_id=vod_session_id(video_id),
            created_at=analysis.video_created_at,
            duration_seconds=duration,
            bucket_seconds=bucket,
        )
    except Exception:
        logger.exception("Failed to load VOD activity for %s from ClickHouse", video_id)
        buckets = []
    return VodActivity(video_id=video_id, bucket_seconds=bucket, duration_seconds=duration, buckets=buckets)


@router.post(
    "/api/vods/{video_id}/label",
    response_model=VodAnalysis,
    dependencies=[Depends(require_api_key)],
)
async def label_vod(
    video_id: str,
    service: VodLabelService = Depends(get_vod_label_service),
) -> VodAnalysis:
    try:
        return await service.label(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to label VOD peaks for %s", video_id)
        raise HTTPException(status_code=500, detail="Failed to label VOD peaks") from exc


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


@router.post("/api/messages", response_model=InsertResult, dependencies=[Depends(require_api_key)])
async def insert_messages(
    messages: list[ChatMessage],
    clickhouse: ClickHouseRepository = Depends(get_clickhouse),
) -> InsertResult:
    if len(messages) > MAX_MESSAGE_BATCH_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Too many messages in one request (max {MAX_MESSAGE_BATCH_SIZE})",
        )
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
        raise HTTPException(status_code=503, detail="Summary context is temporarily unavailable") from exc
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


@router.post("/api/summaries", response_model=InsertResult, dependencies=[Depends(require_api_key)])
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


@router.post("/api/summaries/generate", response_model=ChatSummary, dependencies=[Depends(require_api_key)])
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
                    if data is None:
                        # Sentinel: the hub dropped this subscriber (queue overflow).
                        # End the stream so the client's EventSource reconnects.
                        break
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
