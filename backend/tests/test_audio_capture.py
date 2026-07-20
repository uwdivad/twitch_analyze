from datetime import UTC, datetime
from pathlib import Path

from app.workers.audio_capture import build_ffmpeg_command, build_streamlink_command, parse_chunk_started_at


def test_build_streamlink_command_uses_channel_url_and_stdout() -> None:
    command = build_streamlink_command("example")

    assert command == ["streamlink", "--stdout", "https://twitch.tv/example", "best"]


def test_build_ffmpeg_command_extracts_segmented_mono_audio() -> None:
    command = build_ffmpeg_command(Path("/tmp/audio/%Y%m%dT%H%M%SZ.wav"), 30)

    assert command == [
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
        "30",
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        str(Path("/tmp/audio/%Y%m%dT%H%M%SZ.wav")),
    ]


def test_parse_chunk_started_at_reads_utc_filename() -> None:
    started_at = parse_chunk_started_at(Path("/tmp/audio/20260507T153000Z.wav"))

    assert started_at == datetime(2026, 5, 7, 15, 30, tzinfo=UTC)
