import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import router
from app.core.config import get_settings
from app.core.metrics import CHAT_MESSAGES_INGESTED, INGESTION_CONNECTED
from app.ingestion.irc import TwitchIrcClient
from app.ingestion.twitch import TwitchEventSubClient, resolve_twitch_user_id
from app.models.chat import ChannelInfo, ChatMessage
from app.storage.clickhouse import ClickHouseRepository
from app.storage.kafka import KafkaChatProducer
from app.storage.realtime import RealtimeHub

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    app.state.channels = {
        login: ChannelInfo(channel_id="", channel_login=login, channel_display_name=login, status="configured")
        for login in settings.channel_logins
    }
    app.state.realtime_hub = RealtimeHub(recent_limit=settings.recent_message_limit)
    app.state.clickhouse = await ClickHouseRepository.connect(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_username,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
    )
    try:
        # Idempotent CREATE TABLE IF NOT EXISTS for the VOD analysis tables, so existing
        # ClickHouse volumes (initialized before VOD support) gain them on startup.
        await app.state.clickhouse.ensure_vod_schema()
    except Exception:
        logger.warning("Failed to ensure VOD analysis schema in ClickHouse", exc_info=True)
    app.state.kafka = KafkaChatProducer(settings.kafka_bootstrap_servers, settings.kafka_chat_topic)
    app.state.ingestion_task = None
    app.state.transcription_jobs = {}
    app.state.transcription_tasks = {}
    app.state.vod_jobs = {}
    app.state.vod_tasks = {}

    await app.state.kafka.start()

    async def handle_message(message: ChatMessage) -> None:
        source = str(message.raw_event.get("source", "eventsub"))
        CHAT_MESSAGES_INGESTED.labels(source=source, channel=message.channel_login).inc()
        try:
            await app.state.kafka.publish(message)
        except Exception:
            # A Kafka publish failure must not tear down the ingestion connection,
            # and the live feed should still receive the message.
            logger.exception("Failed to publish chat message %s to Kafka", message.message_id)
        await app.state.realtime_hub.publish_message(message)

    async def handle_status(status: dict) -> None:
        state = status.get("state", "")
        if state in {"irc_connected", "connected"}:
            INGESTION_CONNECTED.labels(mode=settings.twitch_ingestion_mode).set(1)
        elif state in {"irc_error", "error", "revoked"}:
            INGESTION_CONNECTED.labels(mode=settings.twitch_ingestion_mode).set(0)
        channel = status.get("channel")
        if channel:
            info = ChannelInfo(**channel)
            app.state.channels[info.channel_login] = info
        await app.state.realtime_hub.broadcast_status(status)

    if settings.enable_twitch_ingestion and settings.twitch_configured:
        if settings.twitch_ingestion_mode == "irc":
            client = TwitchIrcClient(
                channels=settings.channel_logins,
                username=settings.twitch_username,
                access_token=settings.twitch_access_token if settings.twitch_username else "",
                on_message=handle_message,
                on_status=handle_status,
            )
        else:
            user_id = settings.twitch_user_id or await resolve_twitch_user_id(
                settings.twitch_client_id,
                settings.twitch_access_token,
            )
            client = TwitchEventSubClient(
                client_id=settings.twitch_client_id,
                access_token=settings.twitch_access_token,
                channels=settings.channel_logins,
                user_id=user_id,
                on_message=handle_message,
                on_status=handle_status,
            )
        logger.info("Starting Twitch ingestion mode: %s", settings.twitch_ingestion_mode)
        app.state.twitch_client = client
        app.state.ingestion_task = asyncio.create_task(client.run_forever())
    else:
        INGESTION_CONNECTED.labels(mode=settings.twitch_ingestion_mode).set(0)
        logger.warning("Twitch ingestion disabled or not configured")

    try:
        yield
    finally:
        # Each shutdown step is independently protected so one failure (e.g. an
        # ingestion task that stored a non-cancellation exception) cannot skip the
        # remaining cleanup.
        task = app.state.ingestion_task
        if task:
            INGESTION_CONNECTED.labels(mode=settings.twitch_ingestion_mode).set(0)
            try:
                await app.state.twitch_client.stop()
            except Exception:
                logger.exception("Failed to stop Twitch client during shutdown")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Ingestion task raised during shutdown")
        transcription_tasks = list(app.state.transcription_tasks.values())
        for transcription_task in transcription_tasks:
            transcription_task.cancel()
        for transcription_task in transcription_tasks:
            try:
                await transcription_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Transcription task raised during shutdown")
        vod_tasks = list(app.state.vod_tasks.values())
        for vod_task in vod_tasks:
            vod_task.cancel()
        for vod_task in vod_tasks:
            try:
                await vod_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("VOD analysis task raised during shutdown")
        try:
            await app.state.kafka.stop()
        except Exception:
            logger.exception("Failed to stop Kafka producer during shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Twitch Analyze API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # No cookie/session auth exists; disabling credentials avoids the
        # credentialed-wildcard-origin footgun if CORS_ORIGINS is ever set to "*".
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    return app


app = create_app()
