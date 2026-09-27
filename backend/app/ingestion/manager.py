import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import Settings
from app.core.metrics import INGESTION_CONNECTED
from app.ingestion.irc import TwitchIrcClient
from app.ingestion.twitch import TwitchEventSubClient, resolve_twitch_user_id
from app.models.chat import ChannelInfo, ChatMessage

logger = logging.getLogger(__name__)


class IngestionManager:
    """Owns the running Twitch client so settings changes can restart it in place."""

    def __init__(
        self,
        channels: dict[str, ChannelInfo],
        on_message: Callable[[ChatMessage], Awaitable[None]],
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        # Shared with app.state.channels; mutated in place so readers see updates.
        self.channels = channels
        self._on_message = on_message
        self._on_status = on_status
        self._client: TwitchIrcClient | TwitchEventSubClient | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.mode = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, settings: Settings) -> None:
        async with self._lock:
            await self._start(settings)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def restart(self, settings: Settings) -> None:
        async with self._lock:
            await self._stop()
            await self._start(settings)

    async def handle_status(self, status: dict[str, Any]) -> None:
        state = status.get("state", "")
        if state in {"irc_connected", "connected"}:
            INGESTION_CONNECTED.labels(mode=self.mode).set(1)
        elif state in {"irc_error", "error", "revoked"}:
            INGESTION_CONNECTED.labels(mode=self.mode).set(0)
        channel = status.get("channel")
        if channel:
            info = ChannelInfo(**channel)
            # Ignore late status from a channel removed by a restart.
            if info.channel_login in self.channels:
                self.channels[info.channel_login] = info
        await self._on_status(status)

    async def _start(self, settings: Settings) -> None:
        self.mode = settings.twitch_ingestion_mode
        self.channels.clear()
        self.channels.update(
            {
                login: ChannelInfo(channel_id="", channel_login=login, channel_display_name=login, status="configured")
                for login in settings.channel_logins
            }
        )

        if not (settings.enable_twitch_ingestion and settings.twitch_configured):
            INGESTION_CONNECTED.labels(mode=self.mode).set(0)
            logger.warning("Twitch ingestion disabled or not configured")
            return

        if settings.twitch_ingestion_mode == "irc":
            client: TwitchIrcClient | TwitchEventSubClient = TwitchIrcClient(
                channels=settings.channel_logins,
                username=settings.twitch_username,
                access_token=settings.twitch_access_token if settings.twitch_username else "",
                on_message=self._on_message,
                on_status=self.handle_status,
            )
        else:
            try:
                user_id = settings.twitch_user_id or await resolve_twitch_user_id(
                    settings.twitch_client_id,
                    settings.twitch_access_token,
                )
            except Exception:
                # A bad token must not take down the API (or the settings save that
                # triggered this restart); ingestion simply stays stopped.
                logger.exception("Failed to resolve Twitch user id; EventSub ingestion not started")
                INGESTION_CONNECTED.labels(mode=self.mode).set(0)
                return
            client = TwitchEventSubClient(
                client_id=settings.twitch_client_id,
                access_token=settings.twitch_access_token,
                channels=settings.channel_logins,
                user_id=user_id,
                on_message=self._on_message,
                on_status=self.handle_status,
            )
        logger.info("Starting Twitch ingestion mode: %s", settings.twitch_ingestion_mode)
        self._client = client
        self._task = asyncio.create_task(client.run_forever())

    async def _stop(self) -> None:
        # Each step is independently protected so one failure (e.g. an ingestion
        # task that stored a non-cancellation exception) cannot skip the rest.
        task, client = self._task, self._client
        self._task = self._client = None
        if self.mode:
            INGESTION_CONNECTED.labels(mode=self.mode).set(0)
        if task is None:
            return
        if client is not None:
            try:
                await client.stop()
            except Exception:
                logger.exception("Failed to stop Twitch client")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Ingestion task raised while stopping")
