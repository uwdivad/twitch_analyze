"""Save the last N minutes of a channel's in-progress live VOD as an MP4.

Pipeline for one ``ClipJob``:

1. resolve: ask the platform source (``LiveVodSource``) which archive VOD is being
   recorded for the live channel.
2. stream url: let streamlink resolve the VOD's HLS playlist URL (it owns the
   undocumented playback-token dance, same tool the audio capture uses).
3. tail: fetch the media playlist, keep the shortest suffix of segments covering the
   requested duration, and write it as a local VOD playlist.
4. download: ffmpeg copies those segments into an MP4 (no re-encode) while reporting
   progress; the finished file and a JSON sidecar land in ``clip_output_dir``.

Completed jobs are reloaded from their sidecars at startup, so the clip list
survives restarts as long as the files do. Nothing here touches Kafka or ClickHouse:
clips are files, not chat data.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from app.core.config import Settings
from app.ingestion.hls import (
    Segment,
    best_variant_url,
    is_master_playlist,
    parse_media_playlist,
    render_playlist,
    tail_segments,
)
from app.ingestion.live_vod import (
    ChannelNotFoundError,
    ChannelNotLiveError,
    LiveVodSource,
    NoLiveVodError,
)
from app.models.clips import ClipJob, LiveVod

logger = logging.getLogger(__name__)

__all__ = [
    "ClipFailure",
    "ClipService",
    "build_ffmpeg_command",
    "build_stream_url_command",
    "clip_file_name",
    "load_clip_jobs",
    "parse_ffmpeg_progress_line",
    "vod_page_url",
]

PLAYLIST_FETCH_TIMEOUT_SECONDS = 30
STDERR_TAIL_LINES = 30
# How much of a stderr/last-line tool message is surfaced on the job record.
MAX_ERROR_CHARS = 200

ProgressFn = Callable[[float], None]
StreamUrlFn = Callable[[str, str, float], Awaitable[str]]
FetchTextFn = Callable[[str], Awaitable[str]]
FfmpegFn = Callable[[Path, Path, float, ProgressFn], Awaitable[None]]

_OUT_TIME_RE = re.compile(r"^out_time=(\d+):(\d{2}):(\d{2})(?:\.(\d+))?\s*$")
_OUT_TIME_US_RE = re.compile(r"^out_time_us=(-?\d+)\s*$")


class ClipFailure(Exception):
    """Expected failure with a message safe to show to the user."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def vod_page_url(vod: LiveVod) -> str:
    if vod.platform == "twitch":
        return f"https://www.twitch.tv/videos/{vod.video_id}"
    raise ClipFailure(f"Unsupported platform: {vod.platform}")


def build_stream_url_command(page_url: str, quality: str = "best") -> list[str]:
    return ["streamlink", "--stream-url", page_url, quality]


def build_ffmpeg_command(playlist_path: Path, output_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-y",
        "-progress",
        "pipe:1",
        # The local playlist is a file whose segments are https URLs.
        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto",
        "-i",
        str(playlist_path),
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(output_path),
    ]


def parse_ffmpeg_progress_line(line: str) -> float | None:
    """Seconds of output written so far, from one ``-progress`` key=value line."""
    text = line.strip()
    match = _OUT_TIME_US_RE.match(text)
    if match:
        return max(int(match.group(1)), 0) / 1_000_000
    match = _OUT_TIME_RE.match(text)
    if match:
        hours, minutes, seconds, fraction = match.groups()
        value = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        if fraction:
            value += int(fraction[:6].ljust(6, "0")) / 1_000_000
        return float(value)
    return None


def _hms(seconds: float) -> str:
    total = max(int(seconds), 0)
    return f"{total // 3600}h{(total % 3600) // 60:02d}m{total % 60:02d}s"


def clip_file_name(vod: LiveVod, start_seconds: float, end_seconds: float) -> str:
    """Download name such as ``xqc_20260508_1h02m30s-1h07m30s.mp4``."""
    login = re.sub(r"[^a-z0-9_]+", "", vod.channel_login.lower()) or "clip"
    return f"{login}_{vod.created_at:%Y%m%d}_{_hms(start_seconds)}-{_hms(end_seconds)}.mp4"


def load_clip_jobs(output_dir: Path) -> dict[str, ClipJob]:
    """Completed jobs recorded in ``output_dir`` sidecars whose MP4 still exists."""
    jobs: dict[str, ClipJob] = {}
    if not output_dir.is_dir():
        return jobs
    for sidecar in sorted(output_dir.glob("*.json")):
        try:
            job = ClipJob.model_validate_json(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Skipping unreadable clip sidecar %s", sidecar, exc_info=True)
            continue
        if job.status != "completed" or not (output_dir / f"{job.job_id}.mp4").is_file():
            continue
        jobs[job.job_id] = job
    return dict(sorted(jobs.items(), key=lambda item: item[1].started_at))


# ---------------------------------------------------------------------------
# Default collaborators (subprocesses + HTTP)
#
# The tools run through the blocking ``subprocess`` API in worker threads rather
# than ``asyncio.create_subprocess_exec``: uvicorn on Windows serves from a selector
# event loop, which has no subprocess support (NotImplementedError), and this way the
# code behaves the same on every platform and loop.
# ---------------------------------------------------------------------------


def _last_line(lines: deque[str]) -> str:
    for line in reversed(lines):
        if line.strip():
            return line.strip()[:MAX_ERROR_CHARS]
    return ""


def _kill(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with suppress(OSError):
            process.kill()


def _run_tool(args: list[str], timeout_seconds: float, *, tool: str) -> subprocess.CompletedProcess[bytes]:
    """Blocking helper: run a tool to completion, capturing stdout and stderr."""
    try:
        return subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ClipFailure(f"{tool} is not installed on the backend host") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClipFailure(f"Timed out running {tool}") from exc


async def streamlink_stream_url(page_url: str, quality: str, timeout_seconds: float) -> str:
    """Resolve the HLS playlist URL for a VOD page with ``streamlink --stream-url``."""
    command = build_stream_url_command(page_url, quality)
    try:
        completed = await asyncio.to_thread(_run_tool, command, timeout_seconds, tool="streamlink")
    except ClipFailure as exc:
        if "Timed out" in str(exc):
            raise ClipFailure("Timed out resolving the VOD stream URL") from exc
        raise
    output = completed.stdout.decode("utf-8", errors="replace")
    urls = [line.strip() for line in output.splitlines() if line.strip().startswith("http")]
    if completed.returncode != 0 or not urls:
        errors = deque(completed.stderr.decode("utf-8", errors="replace").splitlines(), maxlen=STDERR_TAIL_LINES)
        detail = _last_line(errors) or f"streamlink exited with {completed.returncode}"
        raise ClipFailure(f"Could not resolve the VOD stream: {detail}")
    return urls[-1]


async def fetch_text(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=PLAYLIST_FETCH_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status >= 400:
                raise ClipFailure(f"Playlist request failed with HTTP {response.status}")
            return await response.text()


async def run_ffmpeg(playlist_path: Path, output_path: Path, timeout_seconds: float, on_progress: ProgressFn) -> None:
    """Copy the playlist's segments into ``output_path``; reports progress in seconds."""
    loop = asyncio.get_running_loop()
    try:
        process = subprocess.Popen(
            build_ffmpeg_command(playlist_path, output_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ClipFailure("ffmpeg is not installed on the backend host") from exc
    assert process.stdout is not None and process.stderr is not None

    errors: deque[str] = deque(maxlen=STDERR_TAIL_LINES)

    def _read_stderr() -> None:
        for raw in process.stderr:  # type: ignore[union-attr]
            errors.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))

    def _read_progress() -> None:
        for raw in process.stdout:  # type: ignore[union-attr]
            seconds = parse_ffmpeg_progress_line(raw.decode("utf-8", errors="replace"))
            if seconds is not None:
                # Job updates must happen on the event loop thread.
                loop.call_soon_threadsafe(on_progress, seconds)

    stderr_reader = loop.run_in_executor(None, _read_stderr)
    progress_reader = loop.run_in_executor(None, _read_progress)
    waiter = loop.run_in_executor(None, process.wait)
    try:
        await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout_seconds)
    except TimeoutError as exc:
        _kill(process)
        raise ClipFailure("Timed out downloading the clip") from exc
    except asyncio.CancelledError:
        _kill(process)
        raise
    finally:
        # The pipes hit EOF once the process is gone, so the readers finish promptly.
        with suppress(Exception):
            await asyncio.wait_for(
                asyncio.gather(waiter, stderr_reader, progress_reader, return_exceptions=True), timeout=5
            )

    if process.returncode != 0:
        detail = _last_line(errors) or f"ffmpeg exited with {process.returncode}"
        raise ClipFailure(f"ffmpeg failed: {detail}")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ClipService:
    def __init__(
        self,
        *,
        settings: Settings,
        jobs: dict[str, ClipJob],
        sources: dict[str, LiveVodSource],
        output_dir: Path | None = None,
        stream_url: StreamUrlFn | None = None,
        fetch: FetchTextFn | None = None,
        ffmpeg: FfmpegFn | None = None,
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._sources = sources
        self._output_dir = output_dir or Path(settings.clip_output_dir)
        self._stream_url = stream_url or streamlink_stream_url
        self._fetch = fetch or fetch_text
        self._ffmpeg = ffmpeg or run_ffmpeg

    # -- paths ----------------------------------------------------------------

    def video_path(self, job_id: str) -> Path:
        return self._output_dir / f"{job_id}.mp4"

    def _playlist_path(self, job_id: str) -> Path:
        return self._output_dir / f"{job_id}.m3u8"

    def _part_path(self, job_id: str) -> Path:
        return self._output_dir / f"{job_id}.part"

    def _sidecar_path(self, job_id: str) -> Path:
        return self._output_dir / f"{job_id}.json"

    # -- job bookkeeping ------------------------------------------------------

    def _update(self, job_id: str, **changes: Any) -> ClipJob:
        current = self._jobs.get(job_id)
        if current is None:
            raise KeyError(job_id)
        updated = current.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
        self._jobs[job_id] = updated
        return updated

    def _remove_files(self, job_id: str, *, keep_video: bool) -> None:
        paths = [self._playlist_path(job_id), self._part_path(job_id)]
        if not keep_video:
            paths += [self.video_path(job_id), self._sidecar_path(job_id)]
        for path in paths:
            with suppress(FileNotFoundError, OSError):
                path.unlink()

    def delete(self, job_id: str) -> None:
        """Forget a job and delete its files (the caller cancels any running task)."""
        self._jobs.pop(job_id, None)
        self._remove_files(job_id, keep_video=False)

    # -- entry point ----------------------------------------------------------

    async def run(self, job_id: str) -> None:
        """Run the whole pipeline for one job. Never raises (except on cancellation)."""
        try:
            await self._run(job_id)
        except asyncio.CancelledError:
            self._update(job_id, status="failed", error="Cancelled", detail="Job cancelled")
            self._remove_files(job_id, keep_video=False)
            raise
        except ClipFailure as exc:
            logger.warning("Clip job %s failed: %s", job_id, exc)
            self._update(job_id, status="failed", error=str(exc), detail="Clip failed")
            self._remove_files(job_id, keep_video=False)
        except Exception as exc:
            logger.exception("Clip job %s failed", job_id)
            # Full detail stays in server logs; the job gets a sanitized error
            # (same precedent as the transcription and VOD jobs).
            self._update(job_id, status="failed", error=type(exc).__name__, detail="Clip failed")
            self._remove_files(job_id, keep_video=False)

    async def _run(self, job_id: str) -> None:
        job = self._jobs[job_id]
        source = self._sources.get(job.platform)
        if source is None:
            raise ClipFailure(f"Unsupported platform: {job.platform}")

        self._update(job_id, status="resolving", detail="Looking up the current live VOD", error="")
        try:
            vod = await source.resolve(job.channel_login)
        except (ChannelNotFoundError, ChannelNotLiveError, NoLiveVodError) as exc:
            raise ClipFailure(str(exc)) from exc
        except RuntimeError as exc:
            # The GQL client raises RuntimeError with safe, user-meaningful text.
            raise ClipFailure(f"Live VOD lookup failed: {str(exc)[:MAX_ERROR_CHARS]}") from exc
        self._update(
            job_id,
            video_id=vod.video_id,
            video_title=vod.title,
            video_created_at=vod.created_at,
            channel_login=vod.channel_login,
            channel_display_name=vod.channel_display_name,
            detail="Resolving the VOD stream URL",
        )

        playlist_url = await self._stream_url(
            vod_page_url(vod), self._settings.clip_stream_quality, self._settings.clip_resolve_timeout_seconds
        )
        segments = await self._load_segments(playlist_url)
        if not segments:
            raise ClipFailure("The VOD playlist has no downloadable segments yet")

        total = sum(segment.duration for segment in segments)
        tail = tail_segments(segments, job.requested_seconds)
        clip_duration = sum(segment.duration for segment in tail)
        clip_end = total
        clip_start = max(total - clip_duration, 0.0)

        self._output_dir.mkdir(parents=True, exist_ok=True)
        playlist_path = self._playlist_path(job_id)
        playlist_path.write_text(render_playlist(tail), encoding="utf-8")
        self._update(
            job_id,
            status="downloading",
            clip_start_seconds=round(clip_start, 3),
            clip_end_seconds=round(clip_end, 3),
            clip_duration_seconds=round(clip_duration, 3),
            downloaded_seconds=0.0,
            detail=f"Downloading {len(tail)} segments ({_hms(clip_duration)})",
        )

        part_path = self._part_path(job_id)
        timeout = max(self._settings.clip_download_timeout_seconds, clip_duration * 2)

        def on_progress(seconds: float) -> None:
            if job_id in self._jobs:
                self._update(job_id, downloaded_seconds=round(min(seconds, clip_duration), 3))

        await self._ffmpeg(playlist_path, part_path, timeout, on_progress)

        video_path = self.video_path(job_id)
        part_path.replace(video_path)
        with suppress(FileNotFoundError, OSError):
            playlist_path.unlink()
        size = video_path.stat().st_size
        completed = self._update(
            job_id,
            status="completed",
            downloaded_seconds=round(clip_duration, 3),
            file_name=clip_file_name(vod, clip_start, clip_end),
            file_size_bytes=size,
            detail=f"Saved {_hms(clip_duration)} ({size / 1_000_000:.1f} MB)",
        )
        self._sidecar_path(job_id).write_text(completed.model_dump_json(indent=2), encoding="utf-8")

    async def _load_segments(self, playlist_url: str) -> list[Segment]:
        text = await self._fetch(playlist_url)
        if is_master_playlist(text):
            variant = best_variant_url(text, playlist_url)
            if not variant:
                raise ClipFailure("The VOD master playlist lists no variants")
            playlist_url = variant
            text = await self._fetch(playlist_url)
        return parse_media_playlist(text, playlist_url)
