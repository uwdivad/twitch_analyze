import asyncio
import logging
import os
import signal
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from openai import OpenAI

from app.core.config import get_settings
from app.models.chat import TranscriptSegment
from app.storage.kafka import KafkaJsonProducer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioChunk:
    channel_login: str
    path: Path
    started_at: datetime
    ended_at: datetime


def build_streamlink_command(channel_login: str) -> list[str]:
    return ["streamlink", "--stdout", f"https://twitch.tv/{channel_login}", "best"]


def build_ffmpeg_command(output_pattern: Path, segment_seconds: int) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        "pipe:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        str(output_pattern),
    ]


def parse_chunk_started_at(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


class AudioCaptureWorker:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._stop = asyncio.Event()
        self._producer = KafkaJsonProducer(settings.kafka_bootstrap_servers, settings.kafka_transcript_topic)
        self._openai = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    async def run(self) -> None:
        if not self._settings.enable_audio_capture:
            logger.info("Audio capture is disabled")
            return
        if not self._settings.audio_channel_logins:
            logger.warning("Audio capture enabled but no channels are configured")
            return
        if self._openai is None:
            logger.error("Audio capture requires OPENAI_API_KEY for transcription")
            return

        await self._producer.start()
        tasks = [
            asyncio.create_task(self._run_channel(channel_login))
            for channel_login in self._settings.audio_channel_logins
        ]
        try:
            await self._stop.wait()
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            await self._producer.stop()

    def stop(self) -> None:
        self._stop.set()

    async def _run_channel(self, channel_login: str) -> None:
        backoff_seconds = 5
        processed: set[Path] = set()
        channel_dir = Path(self._settings.audio_chunk_dir) / channel_login
        channel_dir.mkdir(parents=True, exist_ok=True)

        while not self._stop.is_set():
            try:
                await self._capture_until_exit(channel_login, channel_dir, processed)
                # Keep chunk processing/cleanup inside the protected retry loop so a
                # transient Kafka publish error cannot silently kill this channel's task.
                await self._process_ready_chunks(channel_login, channel_dir, processed)
                await self._cleanup_expired_chunks(channel_dir, processed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Audio capture failed for %s", channel_login)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff_seconds)
            except TimeoutError:
                pass

    async def _capture_until_exit(self, channel_login: str, channel_dir: Path, processed: set[Path]) -> None:
        output_pattern = channel_dir / "%Y%m%dT%H%M%SZ.wav"
        env = dict(os.environ)
        env["TZ"] = "UTC"
        streamlink = await asyncio.create_subprocess_exec(
            *build_streamlink_command(channel_login),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        if streamlink.stdout is None:
            raise RuntimeError("Streamlink stdout pipe was not created")
        ffmpeg = await asyncio.create_subprocess_exec(
            *build_ffmpeg_command(output_pattern, self._settings.audio_segment_seconds),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        logger.info("Started audio capture for %s", channel_login)
        streamlink_stderr_task, streamlink_stderr = self._start_stderr_drain(streamlink)
        ffmpeg_stderr_task, ffmpeg_stderr = self._start_stderr_drain(ffmpeg)
        pump_task = asyncio.create_task(self._pump_stream(streamlink, ffmpeg))
        streamlink_wait = asyncio.create_task(streamlink.wait())
        ffmpeg_wait = asyncio.create_task(ffmpeg.wait())

        try:
            while not self._stop.is_set():
                done, _ = await asyncio.wait(
                    {streamlink_wait, ffmpeg_wait},
                    timeout=2,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                await self._process_ready_chunks(channel_login, channel_dir, processed)
                await self._cleanup_expired_chunks(channel_dir, processed)
                if done:
                    break
        finally:
            pump_task.cancel()
            with suppress(asyncio.CancelledError):
                await pump_task
            await self._terminate_process(ffmpeg)
            await self._terminate_process(streamlink)
            for wait_task in (streamlink_wait, ffmpeg_wait):
                if not wait_task.done():
                    wait_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await wait_task
            await self._finish_stderr_drain(streamlink_stderr_task)
            await self._finish_stderr_drain(ffmpeg_stderr_task)

        streamlink_error = "\n".join(streamlink_stderr).strip()
        ffmpeg_error = "\n".join(ffmpeg_stderr).strip()
        if streamlink.returncode not in (0, None):
            logger.warning("Streamlink exited for %s with %s: %s", channel_login, streamlink.returncode, streamlink_error)
        if ffmpeg.returncode not in (0, None):
            logger.warning("FFmpeg exited for %s with %s: %s", channel_login, ffmpeg.returncode, ffmpeg_error)

    async def capture_channel_for_duration(self, channel_login: str, duration_seconds: int) -> int:
        if self._openai is None:
            raise RuntimeError("OPENAI_API_KEY is required for transcription")

        await self._producer.start()
        processed: set[Path] = set()
        channel_dir = Path(self._settings.audio_chunk_dir) / channel_login
        channel_dir.mkdir(parents=True, exist_ok=True)
        before = set(channel_dir.glob("*.wav"))
        try:
            await self._capture_for_duration(channel_login, channel_dir, processed, before, duration_seconds)
            await self._process_all_chunks(channel_login, channel_dir, processed, before)
        finally:
            await self._producer.stop()
            await self._cleanup_expired_chunks(channel_dir, processed)
        return len(processed)

    async def _capture_for_duration(
        self,
        channel_login: str,
        channel_dir: Path,
        processed: set[Path],
        existing: set[Path],
        duration_seconds: int,
    ) -> None:
        output_pattern = channel_dir / "%Y%m%dT%H%M%SZ.wav"
        env = dict(os.environ)
        env["TZ"] = "UTC"
        streamlink = await asyncio.create_subprocess_exec(
            *build_streamlink_command(channel_login),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        if streamlink.stdout is None:
            raise RuntimeError("Streamlink stdout pipe was not created")
        ffmpeg = await asyncio.create_subprocess_exec(
            *build_ffmpeg_command(output_pattern, self._settings.audio_segment_seconds),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        streamlink_stderr_task, streamlink_stderr = self._start_stderr_drain(streamlink)
        ffmpeg_stderr_task, ffmpeg_stderr = self._start_stderr_drain(ffmpeg)
        pump_task = asyncio.create_task(self._pump_stream(streamlink, ffmpeg))
        streamlink_wait = asyncio.create_task(streamlink.wait())
        ffmpeg_wait = asyncio.create_task(ffmpeg.wait())
        deadline = asyncio.get_running_loop().time() + duration_seconds
        logger.info("Started timed audio capture for %s (%ss)", channel_login, duration_seconds)

        try:
            while not self._stop.is_set():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                done, _ = await asyncio.wait(
                    {streamlink_wait, ffmpeg_wait},
                    timeout=min(2, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                await self._process_new_ready_chunks(channel_login, channel_dir, processed, existing)
                if done:
                    break
        finally:
            pump_task.cancel()
            with suppress(asyncio.CancelledError):
                await pump_task
            await self._terminate_process(ffmpeg)
            await self._terminate_process(streamlink)
            for wait_task in (streamlink_wait, ffmpeg_wait):
                if not wait_task.done():
                    wait_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await wait_task
            await self._finish_stderr_drain(streamlink_stderr_task)
            await self._finish_stderr_drain(ffmpeg_stderr_task)

        streamlink_error = "\n".join(streamlink_stderr).strip()
        ffmpeg_error = "\n".join(ffmpeg_stderr).strip()
        if streamlink.returncode not in (0, None):
            logger.warning("Streamlink exited for %s with %s: %s", channel_login, streamlink.returncode, streamlink_error)
        if ffmpeg.returncode not in (0, None):
            logger.warning("FFmpeg exited for %s with %s: %s", channel_login, ffmpeg.returncode, ffmpeg_error)

    async def _process_new_ready_chunks(
        self,
        channel_login: str,
        channel_dir: Path,
        processed: set[Path],
        existing: set[Path],
    ) -> None:
        for path in sorted(channel_dir.glob("*.wav")):
            if path in existing or path in processed or not self._is_ready(path):
                continue
            started_at = parse_chunk_started_at(path)
            if started_at is None:
                processed.add(path)
                logger.warning("Skipping audio chunk with unexpected filename: %s", path)
                continue
            chunk = AudioChunk(
                channel_login=channel_login,
                path=path,
                started_at=started_at,
                ended_at=started_at + timedelta(seconds=self._settings.audio_segment_seconds),
            )
            await self._transcribe_and_publish(chunk)
            processed.add(path)

    async def _process_all_chunks(
        self,
        channel_login: str,
        channel_dir: Path,
        processed: set[Path],
        existing: set[Path],
    ) -> None:
        for path in sorted(channel_dir.glob("*.wav")):
            if path in existing or path in processed:
                continue
            started_at = parse_chunk_started_at(path)
            if started_at is None:
                processed.add(path)
                logger.warning("Skipping audio chunk with unexpected filename: %s", path)
                continue
            chunk = AudioChunk(
                channel_login=channel_login,
                path=path,
                started_at=started_at,
                ended_at=started_at + timedelta(seconds=self._settings.audio_segment_seconds),
            )
            await self._transcribe_and_publish(chunk)
            processed.add(path)

    async def _pump_stream(
        self,
        streamlink: asyncio.subprocess.Process,
        ffmpeg: asyncio.subprocess.Process,
    ) -> None:
        if streamlink.stdout is None or ffmpeg.stdin is None:
            return
        try:
            while not self._stop.is_set():
                data = await streamlink.stdout.read(65536)
                if not data:
                    break
                ffmpeg.stdin.write(data)
                await ffmpeg.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if ffmpeg.stdin is not None:
                ffmpeg.stdin.close()
                with suppress(BrokenPipeError, ConnectionResetError):
                    await ffmpeg.stdin.wait_closed()

    async def _process_ready_chunks(self, channel_login: str, channel_dir: Path, processed: set[Path]) -> None:
        for path in sorted(channel_dir.glob("*.wav")):
            if path in processed or not self._is_ready(path):
                continue
            started_at = parse_chunk_started_at(path)
            if started_at is None:
                processed.add(path)
                logger.warning("Skipping audio chunk with unexpected filename: %s", path)
                continue
            chunk = AudioChunk(
                channel_login=channel_login,
                path=path,
                started_at=started_at,
                ended_at=started_at + timedelta(seconds=self._settings.audio_segment_seconds),
            )
            await self._transcribe_and_publish(chunk)
            processed.add(path)

    def _is_ready(self, path: Path) -> bool:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return False
        if stat.st_size == 0:
            return False
        age_seconds = datetime.now(UTC).timestamp() - stat.st_mtime
        return age_seconds >= 2

    async def _transcribe_and_publish(self, chunk: AudioChunk) -> None:
        assert self._openai is not None
        status = "transcribed"
        text = ""
        error = ""
        try:
            text = await asyncio.to_thread(self._transcribe_file, chunk.path)
        except Exception as exc:
            status = "failed"
            error = str(exc)
            logger.exception("Failed to transcribe %s", chunk.path)

        segment = TranscriptSegment(
            segment_id=str(uuid4()),
            channel_login=chunk.channel_login,
            session_id=f"{chunk.channel_login}:{chunk.started_at.date().isoformat()}",
            segment_started_at=chunk.started_at,
            segment_ended_at=chunk.ended_at,
            audio_path=str(chunk.path),
            transcript_text=text,
            model=self._settings.openai_transcription_model,
            status=status,
            error=error,
        )
        await self._producer.publish(segment)

    def _transcribe_file(self, path: Path) -> str:
        assert self._openai is not None
        with path.open("rb") as audio_file:
            transcription = self._openai.audio.transcriptions.create(
                model=self._settings.openai_transcription_model,
                file=audio_file,
            )
        return str(getattr(transcription, "text", "")).strip()

    async def _cleanup_expired_chunks(self, channel_dir: Path, processed: set[Path]) -> None:
        cutoff = datetime.now(UTC).timestamp() - (self._settings.audio_retention_minutes * 60)
        for path in channel_dir.glob("*.wav"):
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime > cutoff:
                continue
            with suppress(FileNotFoundError):
                path.unlink()
            processed.discard(path)

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _start_stderr_drain(
        self,
        process: asyncio.subprocess.Process,
        max_lines: int = 50,
    ) -> tuple[asyncio.Task | None, deque[str]]:
        """Continuously drain a subprocess stderr pipe into a bounded line buffer.

        Verbose tools (streamlink/ffmpeg) can fill the ~64KB OS pipe buffer and deadlock
        the capture if stderr is never read while the process runs. The returned deque
        keeps only the last `max_lines` lines for error reporting.
        """
        lines: deque[str] = deque(maxlen=max_lines)
        stderr = process.stderr
        if stderr is None:
            return None, lines

        async def _drain() -> None:
            buffer = b""
            try:
                while True:
                    chunk = await stderr.read(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    *complete, buffer = buffer.split(b"\n")
                    for raw_line in complete:
                        lines.append(raw_line.decode("utf-8", errors="replace").rstrip("\r"))
                    if len(buffer) > 65536:
                        lines.append(buffer.decode("utf-8", errors="replace"))
                        buffer = b""
                if buffer:
                    lines.append(buffer.decode("utf-8", errors="replace").rstrip("\r"))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("stderr drain failed", exc_info=True)

        return asyncio.create_task(_drain()), lines

    async def _finish_stderr_drain(self, task: asyncio.Task | None) -> None:
        if task is None:
            return
        # The process has been terminated, so the pipe hits EOF almost immediately;
        # the timeout is just a safety net.
        try:
            await asyncio.wait_for(task, timeout=2)
        except TimeoutError:
            pass
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    worker = AudioCaptureWorker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
