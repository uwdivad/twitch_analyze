import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ingestion.live_vod import ChannelNotLiveError
from app.models.clips import ClipJob, LiveVod
from app.services.clips import (
    ClipFailure,
    ClipService,
    build_ffmpeg_command,
    build_stream_url_command,
    clip_file_name,
    load_clip_jobs,
    parse_ffmpeg_progress_line,
    vod_page_url,
)

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=160000
audio_only/index-dvr.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080
chunked/index-dvr.m3u8
"""


def _media(count: int, duration: float = 10.0) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10", "#EXT-X-PLAYLIST-TYPE:EVENT"]
    for index in range(count):
        lines += [f"#EXTINF:{duration:.3f},", f"{index}.ts"]
    return "\n".join(lines) + "\n"


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, clip_output_dir=str(tmp_path), **overrides)


def _vod() -> LiveVod:
    return LiveVod(
        platform="twitch",
        video_id="300",
        title="Big stream",
        channel_id="1",
        channel_login="xqc",
        channel_display_name="xQc",
        created_at=datetime(2026, 10, 8, 18, 0, tzinfo=UTC),
        duration_seconds=3000,
    )


def _job(job_id: str = "job-1", requested_seconds: int = 300, platform: str = "twitch") -> ClipJob:
    now = datetime(2026, 10, 8, 19, 0, tzinfo=UTC)
    return ClipJob(
        job_id=job_id,
        platform=platform,
        channel_login="xqc",
        requested_seconds=requested_seconds,
        status="queued",
        started_at=now,
        updated_at=now,
    )


class FakeSource:
    platform = "twitch"

    def __init__(self, vod=None, error=None):
        self.vod = vod or _vod()
        self.error = error
        self.calls = []

    async def resolve(self, channel_login):
        self.calls.append(channel_login)
        if self.error is not None:
            raise self.error
        return self.vod


class FakeTools:
    """Fake streamlink / HTTP / ffmpeg collaborators that record what they were given."""

    def __init__(self, *, playlists=None, segments=60, ffmpeg_error=None, ffmpeg_delay=0.0):
        self.playlists = playlists or {
            "https://cdn.example/vod/index.m3u8": MASTER,
            "https://cdn.example/vod/chunked/index-dvr.m3u8": _media(segments),
        }
        self.ffmpeg_error = ffmpeg_error
        self.ffmpeg_delay = ffmpeg_delay
        self.stream_url_calls = []
        self.fetched = []
        self.ffmpeg_calls = []
        self.playlist_text = ""

    async def stream_url(self, page_url, quality, timeout):
        self.stream_url_calls.append((page_url, quality, timeout))
        return "https://cdn.example/vod/index.m3u8"

    async def fetch(self, url):
        self.fetched.append(url)
        return self.playlists[url]

    async def ffmpeg(self, playlist_path, output_path, timeout, on_progress):
        self.ffmpeg_calls.append((playlist_path, output_path, timeout))
        self.playlist_text = playlist_path.read_text(encoding="utf-8")
        if self.ffmpeg_delay:
            await asyncio.sleep(self.ffmpeg_delay)
        if self.ffmpeg_error is not None:
            raise self.ffmpeg_error
        on_progress(12.5)
        on_progress(10_000.0)  # progress is clamped to the clip length
        output_path.write_bytes(b"\x00" * 2048)


def _service(tmp_path: Path, jobs, source=None, tools=None, **overrides) -> tuple[ClipService, FakeTools]:
    tools = tools or FakeTools()
    service = ClipService(
        settings=_settings(tmp_path, **overrides),
        jobs=jobs,
        sources={"twitch": source or FakeSource()},
        stream_url=tools.stream_url,
        fetch=tools.fetch,
        ffmpeg=tools.ffmpeg,
    )
    return service, tools


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_commands() -> None:
    assert build_stream_url_command("https://www.twitch.tv/videos/300") == [
        "streamlink",
        "--stream-url",
        "https://www.twitch.tv/videos/300",
        "best",
    ]
    command = build_ffmpeg_command(Path("/tmp/c/job.m3u8"), Path("/tmp/c/job.part"))
    assert command[0] == "ffmpeg"
    assert command[command.index("-i") + 1] == str(Path("/tmp/c/job.m3u8"))
    assert command[-1] == str(Path("/tmp/c/job.part"))
    assert command[command.index("-protocol_whitelist") + 1] == "file,http,https,tcp,tls,crypto"
    assert command.index("-protocol_whitelist") < command.index("-i")
    assert "-c" in command and command[command.index("-c") + 1] == "copy"
    assert vod_page_url(_vod()) == "https://www.twitch.tv/videos/300"


def test_parse_ffmpeg_progress_line() -> None:
    assert parse_ffmpeg_progress_line("out_time=00:01:02.500000") == 62.5
    assert parse_ffmpeg_progress_line("out_time=01:00:00") == 3600.0
    assert parse_ffmpeg_progress_line("out_time_us=1500000\n") == 1.5
    assert parse_ffmpeg_progress_line("out_time_us=-1") == 0.0
    assert parse_ffmpeg_progress_line("frame=12") is None
    assert parse_ffmpeg_progress_line("progress=continue") is None


def test_clip_file_name() -> None:
    assert clip_file_name(_vod(), 3720.4, 4020.4) == "xqc_20261008_1h02m00s-1h07m00s.mp4"
    weird = _vod().model_copy(update={"channel_login": "We!rd Name"})
    assert clip_file_name(weird, 0, 300).startswith("werdname_")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_saves_tail_of_live_vod(tmp_path: Path) -> None:
    jobs = {"job-1": _job()}
    source = FakeSource()
    service, tools = _service(tmp_path, jobs, source=source, clip_stream_quality="720p60")

    await service.run("job-1")

    job = jobs["job-1"]
    assert job.status == "completed", job.error
    assert job.video_id == "300"
    assert job.video_title == "Big stream"
    assert job.channel_display_name == "xQc"
    assert job.video_created_at == _vod().created_at
    # 60 x 10 s playlist; the last 300 s are the last 30 segments.
    assert job.clip_start_seconds == 300.0
    assert job.clip_end_seconds == 600.0
    assert job.clip_duration_seconds == 300.0
    assert job.downloaded_seconds == 300.0
    assert job.file_name == "xqc_20261008_0h05m00s-0h10m00s.mp4"
    assert job.file_size_bytes == 2048
    assert job.error == ""

    assert source.calls == ["xqc"]
    assert tools.stream_url_calls == [("https://www.twitch.tv/videos/300", "720p60", 60.0)]
    # Master playlist first, then the best variant.
    assert tools.fetched == ["https://cdn.example/vod/index.m3u8", "https://cdn.example/vod/chunked/index-dvr.m3u8"]
    playlist_path, part_path, timeout = tools.ffmpeg_calls[0]
    assert part_path == tmp_path / "job-1.part"
    assert timeout == 600.0
    lines = tools.playlist_text.splitlines()
    assert lines[-1] == "#EXT-X-ENDLIST"
    urls = [line for line in lines if line.startswith("http")]
    assert len(urls) == 30
    assert urls[0] == "https://cdn.example/vod/chunked/30.ts"
    assert urls[-1] == "https://cdn.example/vod/chunked/59.ts"

    assert (tmp_path / "job-1.mp4").read_bytes() == b"\x00" * 2048
    assert not part_path.exists()
    assert not playlist_path.exists()
    sidecar = tmp_path / "job-1.json"
    assert sidecar.is_file()
    assert ClipJob.model_validate_json(sidecar.read_text(encoding="utf-8")) == job
    assert service.video_path("job-1") == tmp_path / "job-1.mp4"


@pytest.mark.anyio
async def test_run_uses_whole_playlist_when_vod_is_shorter_than_requested(tmp_path: Path) -> None:
    jobs = {"job-1": _job(requested_seconds=600)}
    service, tools = _service(tmp_path, jobs, tools=FakeTools(segments=12))

    await service.run("job-1")

    job = jobs["job-1"]
    assert job.status == "completed", job.error
    assert job.clip_start_seconds == 0.0
    assert job.clip_end_seconds == 120.0
    assert job.clip_duration_seconds == 120.0
    assert len([line for line in tools.playlist_text.splitlines() if line.startswith("http")]) == 12


@pytest.mark.anyio
async def test_run_reports_platform_errors_as_user_messages(tmp_path: Path) -> None:
    jobs = {"job-1": _job()}
    service, tools = _service(tmp_path, jobs, source=FakeSource(error=ChannelNotLiveError("xQc is not live right now")))

    await service.run("job-1")

    assert jobs["job-1"].status == "failed"
    assert jobs["job-1"].error == "xQc is not live right now"
    assert tools.stream_url_calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_run_sanitizes_unexpected_errors_and_surfaces_clip_failures(tmp_path: Path) -> None:
    jobs = {"job-1": _job(), "job-2": _job("job-2")}
    service, tools = _service(tmp_path, jobs, tools=FakeTools(ffmpeg_error=ClipFailure("ffmpeg failed: boom")))
    await service.run("job-1")
    assert jobs["job-1"].status == "failed"
    assert jobs["job-1"].error == "ffmpeg failed: boom"
    assert not (tmp_path / "job-1.m3u8").exists()
    assert not (tmp_path / "job-1.part").exists()

    service, tools = _service(tmp_path, jobs, tools=FakeTools(ffmpeg_error=OSError("disk full /secret/path")))
    await service.run("job-2")
    assert jobs["job-2"].status == "failed"
    assert jobs["job-2"].error == "OSError"
    assert "secret" not in jobs["job-2"].error


@pytest.mark.anyio
async def test_run_fails_on_empty_playlist_and_unknown_platform(tmp_path: Path) -> None:
    jobs = {"job-1": _job(), "job-2": _job("job-2", platform="youtube")}
    empty = FakeTools(playlists={"https://cdn.example/vod/index.m3u8": "#EXTM3U\n#EXT-X-VERSION:3\n"})
    service, _ = _service(tmp_path, jobs, tools=empty)

    await service.run("job-1")
    assert jobs["job-1"].status == "failed"
    assert "no downloadable segments" in jobs["job-1"].error

    await service.run("job-2")
    assert jobs["job-2"].status == "failed"
    assert jobs["job-2"].error == "Unsupported platform: youtube"


@pytest.mark.anyio
async def test_cancelled_run_marks_job_failed_and_cleans_up(tmp_path: Path) -> None:
    jobs = {"job-1": _job()}
    service, _ = _service(tmp_path, jobs, tools=FakeTools(ffmpeg_delay=5.0))

    task = asyncio.create_task(service.run("job-1"))
    while jobs["job-1"].status != "downloading":
        await asyncio.sleep(0)
    assert (tmp_path / "job-1.m3u8").exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert jobs["job-1"].status == "failed"
    assert jobs["job-1"].error == "Cancelled"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_delete_removes_job_and_files(tmp_path: Path) -> None:
    jobs = {"job-1": _job()}
    service, _ = _service(tmp_path, jobs)
    await service.run("job-1")
    assert (tmp_path / "job-1.mp4").exists()

    service.delete("job-1")

    assert jobs == {}
    assert list(tmp_path.iterdir()) == []
    service.delete("missing")  # idempotent


# ---------------------------------------------------------------------------
# Real subprocess runners (python stand-ins for streamlink / ffmpeg)
# ---------------------------------------------------------------------------


def _python_command(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.mark.anyio
async def test_run_ffmpeg_reports_progress_and_writes_output(tmp_path: Path, monkeypatch) -> None:
    from app.services import clips as clips_module

    output = tmp_path / "out.part"
    code = (
        "import sys, time\n"
        f"open(sys.argv[1], 'wb').write(b'x' * 10)\n"
        "for t in ('00:00:05.000000', '00:00:12.500000'):\n"
        "    print('frame=1'); print('out_time=' + t); print('progress=continue'); sys.stdout.flush(); time.sleep(0.05)\n"
        "print('warning: fake', file=sys.stderr)\n"
    )
    monkeypatch.setattr(clips_module, "build_ffmpeg_command", lambda playlist, out: _python_command(code) + [str(out)])
    seen: list[float] = []

    await clips_module.run_ffmpeg(tmp_path / "in.m3u8", output, 30.0, seen.append)

    assert output.read_bytes() == b"x" * 10
    assert seen == [5.0, 12.5]


@pytest.mark.anyio
async def test_run_ffmpeg_failure_surfaces_last_stderr_line(tmp_path: Path, monkeypatch) -> None:
    from app.services import clips as clips_module

    code = "import sys; print('first', file=sys.stderr); print('Error opening input files: boom', file=sys.stderr); sys.exit(1)"
    monkeypatch.setattr(clips_module, "build_ffmpeg_command", lambda playlist, out: _python_command(code))

    with pytest.raises(ClipFailure) as exc:
        await clips_module.run_ffmpeg(tmp_path / "in.m3u8", tmp_path / "out.part", 30.0, lambda _s: None)
    assert str(exc.value) == "ffmpeg failed: Error opening input files: boom"


@pytest.mark.anyio
async def test_run_ffmpeg_timeout_and_cancel_kill_the_process(tmp_path: Path, monkeypatch) -> None:
    from app.services import clips as clips_module

    code = "import time; time.sleep(30)"
    monkeypatch.setattr(clips_module, "build_ffmpeg_command", lambda playlist, out: _python_command(code))

    with pytest.raises(ClipFailure, match="Timed out"):
        await clips_module.run_ffmpeg(tmp_path / "in.m3u8", tmp_path / "out.part", 0.3, lambda _s: None)

    task = asyncio.create_task(clips_module.run_ffmpeg(tmp_path / "in.m3u8", tmp_path / "out.part", 30.0, lambda _s: None))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_streamlink_stream_url_parses_output_and_errors(monkeypatch) -> None:
    from app.services import clips as clips_module

    ok = "print('[cli][info] Found matching plugin'); print('https://cdn.example/vod/index.m3u8')"
    monkeypatch.setattr(clips_module, "build_stream_url_command", lambda url, quality: _python_command(ok))
    assert await clips_module.streamlink_stream_url("https://www.twitch.tv/videos/1", "best", 30) == "https://cdn.example/vod/index.m3u8"

    bad = "import sys; print('error: No playable streams found on this URL', file=sys.stderr); sys.exit(1)"
    monkeypatch.setattr(clips_module, "build_stream_url_command", lambda url, quality: _python_command(bad))
    with pytest.raises(ClipFailure) as exc:
        await clips_module.streamlink_stream_url("https://www.twitch.tv/videos/1", "best", 30)
    assert str(exc.value) == "Could not resolve the VOD stream: error: No playable streams found on this URL"

    monkeypatch.setattr(clips_module, "build_stream_url_command", lambda url, quality: ["definitely-not-a-real-tool-xyz"])
    with pytest.raises(ClipFailure, match="not installed"):
        await clips_module.streamlink_stream_url("https://www.twitch.tv/videos/1", "best", 30)


# ---------------------------------------------------------------------------
# Startup reload
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_load_clip_jobs_reads_completed_sidecars_with_files(tmp_path: Path) -> None:
    jobs = {"a": _job("a"), "b": _job("b")}
    service, _ = _service(tmp_path, jobs)
    await service.run("a")
    await service.run("b")
    (tmp_path / "b.mp4").unlink()  # file gone: sidecar must be ignored
    (tmp_path / "c.json").write_text(_job("c").model_dump_json(), encoding="utf-8")  # never completed
    (tmp_path / "d.json").write_text("{not json", encoding="utf-8")

    loaded = load_clip_jobs(tmp_path)

    assert list(loaded) == ["a"]
    assert loaded["a"] == jobs["a"]
    assert load_clip_jobs(tmp_path / "missing") == {}
