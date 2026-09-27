"""Downloadable exports of a stored VOD analysis (full JSON bundle or peaks CSV)."""

import csv
import io
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from app.models.chat import TopItem
from app.models.vod import VodActivity, VodAnalysis

VodExportFormat = Literal["json", "csv"]
# Bump when the JSON bundle shape changes incompatibly.
VOD_EXPORT_VERSION = 1

PEAK_CSV_COLUMNS = [
    "peak_id",
    "start_seconds",
    "end_seconds",
    "peak_seconds",
    "start",
    "end",
    "peak",
    "twitch_url",
    "message_count",
    "peak_bucket_count",
    "messages_per_second",
    "score",
    "baseline",
    "label",
    "title",
    "top_emotes",
    "top_tokens",
    "sample_messages",
]


class VodExport(BaseModel):
    export_version: int = VOD_EXPORT_VERSION
    exported_at: datetime
    analysis: VodAnalysis
    activity: VodActivity


def build_vod_export(analysis: VodAnalysis, activity: VodActivity, exported_at: datetime | None = None) -> VodExport:
    return VodExport(exported_at=exported_at or datetime.now(UTC), analysis=analysis, activity=activity)


def format_hms(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def twitch_timestamp_url(video_id: str, seconds: int) -> str:
    hours, rest = divmod(max(int(seconds), 0), 3600)
    minutes, secs = divmod(rest, 60)
    return f"https://www.twitch.tv/videos/{video_id}?t={hours}h{minutes}m{secs}s"


def _join_top(items: list[TopItem]) -> str:
    return "; ".join(f"{item.value} ({item.count})" for item in items)


def _csv_safe(value: str) -> str:
    # Chat text is user-controlled; keep spreadsheet apps from evaluating it as a formula.
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def vod_peaks_csv(analysis: VodAnalysis) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(PEAK_CSV_COLUMNS)
    for peak in analysis.peaks:
        writer.writerow(
            [
                peak.peak_id,
                peak.start_seconds,
                peak.end_seconds,
                peak.peak_seconds,
                format_hms(peak.start_seconds),
                format_hms(peak.end_seconds),
                format_hms(peak.peak_seconds),
                twitch_timestamp_url(analysis.video_id, peak.start_seconds),
                peak.message_count,
                peak.peak_bucket_count,
                round(peak.messages_per_second, 3),
                round(peak.score, 3),
                round(peak.baseline, 3),
                _csv_safe(peak.label),
                _csv_safe(peak.title),
                _csv_safe(_join_top(peak.top_emotes)),
                _csv_safe(_join_top(peak.top_tokens)),
                _csv_safe(" | ".join(peak.sample_messages)),
            ]
        )
    return buffer.getvalue()


def export_filename(analysis: VodAnalysis, export_format: VodExportFormat) -> str:
    channel = re.sub(r"[^A-Za-z0-9_-]+", "", analysis.channel_login) or "vod"
    video_id = re.sub(r"[^A-Za-z0-9_-]+", "", analysis.video_id) or "unknown"
    suffix = "analysis.json" if export_format == "json" else "peaks.csv"
    return f"{channel}-{video_id}-{suffix}"
