from datetime import UTC, datetime

from app.models.chat import TranscriptSegment
from app.storage.clickhouse import ClickHouseRepository


def test_transcript_segment_row_preserves_storage_fields() -> None:
    segment = TranscriptSegment(
        segment_id="segment-1",
        channel_login="example",
        session_id="example:2026-05-07",
        segment_started_at=datetime(2026, 5, 7, 15, 30, tzinfo=UTC),
        segment_ended_at=datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC),
        audio_path="/tmp/twitch-audio/example/20260507T153000Z.wav",
        transcript_text="hello chat",
        model="gpt-4o-mini-transcribe",
        status="transcribed",
    )

    row = ClickHouseRepository._transcript_segment_row(object(), segment)

    assert row[:10] == (
        "segment-1",
        "example",
        "example:2026-05-07",
        datetime(2026, 5, 7, 15, 30, tzinfo=UTC),
        datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC),
        "/tmp/twitch-audio/example/20260507T153000Z.wav",
        "hello chat",
        "gpt-4o-mini-transcribe",
        "transcribed",
        "",
    )
