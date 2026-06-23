from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.json import dumps
from app.workers.transcript_consumer import transcript_segment_from_record


def test_transcript_segment_from_record_parses_valid_payload() -> None:
    payload = {
        "segment_id": "segment-1",
        "channel_login": "example",
        "session_id": "example:2026-05-07",
        "segment_started_at": datetime(2026, 5, 7, 15, 30, tzinfo=UTC).isoformat(),
        "segment_ended_at": datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC).isoformat(),
        "audio_path": "/tmp/twitch-audio/example/20260507T153000Z.wav",
        "transcript_text": "hello chat",
        "model": "gpt-4o-mini-transcribe",
        "status": "transcribed",
    }
    record = SimpleNamespace(topic="twitch.stream.transcripts", partition=0, offset=1, value=dumps(payload).encode())

    segment = transcript_segment_from_record(record)

    assert segment is not None
    assert segment.segment_id == "segment-1"
    assert segment.transcript_text == "hello chat"


def test_transcript_segment_from_record_skips_invalid_payload() -> None:
    record = SimpleNamespace(topic="twitch.stream.transcripts", partition=0, offset=2, value=b'{"segment_id":"bad"}')

    assert transcript_segment_from_record(record) is None
