import csv
import io
from datetime import UTC, datetime

from app.models.chat import TopItem
from app.models.vod import VodActivity, VodActivityBucket, VodAnalysis, VodPeak
from app.services.vod_export import (
    PEAK_CSV_COLUMNS,
    build_vod_export,
    export_filename,
    format_hms,
    twitch_timestamp_url,
    vod_peaks_csv,
)

NOW = datetime(2026, 5, 1, tzinfo=UTC)


def _analysis(**overrides) -> VodAnalysis:
    peak = VodPeak(
        peak_id=1,
        start_seconds=3725,
        end_seconds=3790,
        peak_seconds=3740,
        message_count=420,
        peak_bucket_count=90,
        messages_per_second=6.4615,
        score=8.12345,
        baseline=1.5,
        top_emotes=[TopItem(value="KEKW", count=50), TopItem(value="LUL", count=12)],
        top_tokens=[TopItem(value="clip", count=9)],
        sample_messages=["alice: =HYPERLINK(\"x\")", "bob: clip it, now"],
        label="KEKW spike",
        title="Chat loses it",
    )
    values = dict(
        video_id="123456",
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        title="Big stream",
        video_created_at=NOW,
        duration_seconds=7200,
        bucket_seconds=5,
        message_count=1000,
        unique_chatter_count=100,
        status="completed",
        peaks=[peak],
        analyzed_at=NOW,
        updated_at=NOW,
    )
    values.update(overrides)
    return VodAnalysis(**values)


def test_format_hms_and_twitch_url() -> None:
    assert format_hms(0) == "0:00:00"
    assert format_hms(3725) == "1:02:05"
    assert format_hms(-5) == "0:00:00"
    assert twitch_timestamp_url("123456", 3725) == "https://www.twitch.tv/videos/123456?t=1h2m5s"


def test_vod_peaks_csv_rows() -> None:
    rows = list(csv.reader(io.StringIO(vod_peaks_csv(_analysis()))))
    assert rows[0] == PEAK_CSV_COLUMNS
    row = dict(zip(rows[0], rows[1]))
    assert row["start"] == "1:02:05"
    assert row["peak"] == "1:02:20"
    assert row["twitch_url"].endswith("?t=1h2m5s")
    assert row["messages_per_second"] == "6.462"
    assert row["top_emotes"] == "KEKW (50); LUL (12)"
    # Chat text containing commas is quoted, not split.
    assert row["sample_messages"] == 'alice: =HYPERLINK("x") | bob: clip it, now'
    assert len(rows) == 2


def test_vod_peaks_csv_neutralizes_formula_cells() -> None:
    analysis = _analysis()
    analysis.peaks[0].label = "=cmd()"
    rows = list(csv.reader(io.StringIO(vod_peaks_csv(analysis))))
    assert dict(zip(rows[0], rows[1]))["label"] == "'=cmd()"


def test_vod_peaks_csv_without_peaks_has_header_only() -> None:
    rows = list(csv.reader(io.StringIO(vod_peaks_csv(_analysis(peaks=[])))))
    assert rows == [PEAK_CSV_COLUMNS]


def test_build_vod_export_bundle() -> None:
    activity = VodActivity(
        video_id="123456",
        bucket_seconds=60,
        duration_seconds=7200,
        buckets=[VodActivityBucket(index=0, offset_seconds=0, message_count=3, unique_chatter_count=2)],
    )
    bundle = build_vod_export(_analysis(), activity, exported_at=NOW)
    payload = bundle.model_dump(mode="json")
    assert payload["export_version"] == 1
    assert payload["exported_at"] == "2026-05-01T00:00:00Z"
    assert payload["analysis"]["peaks"][0]["label"] == "KEKW spike"
    assert payload["activity"]["buckets"][0]["message_count"] == 3


def test_export_filename_sanitizes_parts() -> None:
    assert export_filename(_analysis(), "json") == "example-123456-analysis.json"
    assert export_filename(_analysis(channel_login='bad"name/..'), "csv") == "badname-123456-peaks.csv"
    assert export_filename(_analysis(channel_login=""), "csv") == "vod-123456-peaks.csv"
