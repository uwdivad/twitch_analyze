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


# Imports kept with this appended section so the module header stays untouched.
import asyncio  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402


class RecordingInsertClient:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[tuple[Any, ...]], list[str]]] = []

    def insert(self, table: str, rows: list[tuple[Any, ...]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))


@pytest.mark.anyio
async def test_insert_transcript_segments_columns_align_with_row_values() -> None:
    client = RecordingInsertClient()
    repo = ClickHouseRepository.__new__(ClickHouseRepository)
    repo._database = "twitch_analyze"
    repo._lock = asyncio.Lock()
    repo._client = client
    segment = TranscriptSegment(
        segment_id="segment-1",
        channel_login="example",
        session_id="example:2026-05-07",
        segment_started_at=datetime(2026, 5, 7, 15, 30, tzinfo=UTC),
        segment_ended_at=datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC),
        audio_path="/tmp/twitch-audio/example/20260507T153000Z.wav",
        transcript_text="hello chat",
        model="gpt-4o-mini-transcribe",
        status="failed",
        error="rate limited",
        created_at=datetime(2026, 5, 7, 15, 31, tzinfo=UTC),
    )

    await repo.insert_transcript_segments([segment])
    await repo.insert_transcript_segments([])  # empty batches are not sent

    assert len(client.inserts) == 1
    table, rows, columns = client.inserts[0]
    assert table == "stream_transcript_segments"
    assert len(columns) == len(rows[0])
    assert len(set(columns)) == len(columns)
    row = dict(zip(columns, rows[0], strict=True))
    assert row == {
        "segment_id": "segment-1",
        "channel_login": "example",
        "session_id": "example:2026-05-07",
        "segment_started_at": datetime(2026, 5, 7, 15, 30, tzinfo=UTC),
        "segment_ended_at": datetime(2026, 5, 7, 15, 30, 30, tzinfo=UTC),
        "audio_path": "/tmp/twitch-audio/example/20260507T153000Z.wav",
        "transcript_text": "hello chat",
        "model": "gpt-4o-mini-transcribe",
        "status": "failed",
        "error": "rate limited",
        "created_at": datetime(2026, 5, 7, 15, 31, tzinfo=UTC),
    }
