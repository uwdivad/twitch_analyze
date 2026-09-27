from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.workers.audio_capture import (
    AudioCaptureWorker,
    AudioChunk,
    build_ffmpeg_command,
    build_streamlink_command,
    parse_chunk_started_at,
)


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


@pytest.mark.parametrize(
    "name",
    ["chunk-0001.wav", "20260507T153000.wav", "20261307T153000Z.wav", "2026-05-07T15:30:00Z.wav", ".wav"],
)
def test_parse_chunk_started_at_returns_none_for_bad_filename(name: str) -> None:
    assert parse_chunk_started_at(Path("/tmp/audio") / name) is None


class RecordingProducer:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, segment: Any) -> None:
        self.published.append(segment)


def _audio_worker(transcribe) -> tuple[AudioCaptureWorker, RecordingProducer]:
    worker = AudioCaptureWorker.__new__(AudioCaptureWorker)
    worker._settings = SimpleNamespace(openai_transcription_model="gpt-4o-mini-transcribe")
    worker._openai = object()  # only checked for presence; _transcribe_file is faked
    producer = RecordingProducer()
    worker._producer = producer
    worker._transcribe_file = transcribe  # type: ignore[method-assign]
    return worker, producer


def _chunk() -> AudioChunk:
    return AudioChunk(
        channel_login="example",
        path=Path("/tmp/audio/example/20260507T153000Z.wav"),
        started_at=datetime(2026, 5, 7, 15, 30, tzinfo=UTC),
        ended_at=datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_transcribe_and_publish_publishes_failed_segment_when_transcription_raises() -> None:
    def failing_transcribe(path: Path) -> str:
        raise RuntimeError("audio file is corrupt")

    worker, producer = _audio_worker(failing_transcribe)

    await worker._transcribe_and_publish(_chunk())

    assert len(producer.published) == 1
    segment = producer.published[0]
    assert segment.status == "failed"
    assert segment.error == "audio file is corrupt"
    assert segment.transcript_text == ""
    assert segment.channel_login == "example"
    assert segment.session_id == "example:2026-05-07"
    assert segment.audio_path == "/tmp/audio/example/20260507T153000Z.wav"
    assert segment.model == "gpt-4o-mini-transcribe"


@pytest.mark.anyio
async def test_transcribe_and_publish_publishes_transcribed_segment() -> None:
    calls: list[Path] = []

    def transcribe(path: Path) -> str:
        calls.append(path)
        return "hello chat"

    worker, producer = _audio_worker(transcribe)

    await worker._transcribe_and_publish(_chunk())

    assert calls == [_chunk().path]
    segment = producer.published[0]
    assert segment.status == "transcribed"
    assert segment.error == ""
    assert segment.transcript_text == "hello chat"
    assert segment.segment_started_at == datetime(2026, 5, 7, 15, 30, tzinfo=UTC)
    assert segment.segment_ended_at == datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC)
