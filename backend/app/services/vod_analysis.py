"""VOD chat-peak analysis: fetch -> Kafka -> ClickHouse catch-up -> peak detection -> labels.

The heavy lifting is split into small deterministic helpers (bucket sizing, peak
detection, heuristic labels) and two services:

- ``VodAnalysisService`` drives one VOD through the pipeline and records progress on
  an in-memory ``VodAnalysisJob``. Chat replay comments go through the normal Kafka
  topic and ClickHouse consumer worker; analysis then reads them back from ClickHouse.
- ``VodLabelService`` optionally asks OpenAI for short peak titles, mirroring
  ``SummaryService``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import re
import statistics
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from openai import OpenAI

from app.core.config import Settings
from app.core.metrics import CHAT_MESSAGES_INGESTED
from app.models.chat import ChatMessage, TopItem
from app.models.vod import VodAnalysis, VodAnalysisJob, VodMetadata, VodPeak

logger = logging.getLogger(__name__)

BUCKET_CHOICES: tuple[int, ...] = (5, 10, 15, 30, 60, 120, 300)
MAX_SAMPLE_MESSAGES = 20
FLUSH_EVERY_PAGES = 50
VOD_METRIC_SOURCE = "twitch_vod"

STOPWORDS: frozenset[str] = frozenset(
    {
        # Chat filler.
        "lol", "lmao", "lmfao", "rofl", "omg", "bro", "bruh", "haha", "hahaha", "xd", "wtf", "yeah",
        "yes", "yep", "nah", "nope", "ok", "okay", "guys", "chat", "just", "like", "really", "gonna",
        "wanna", "dont", "cant", "wont", "didnt", "doesnt", "isnt", "thats", "whats", "its", "im",
        "ive", "youre", "hes", "shes", "theyre", "got", "get", "going", "one", "now", "yo", "hey",
        # Common English stopwords.
        "the", "and", "for", "are", "but", "not", "you", "your", "yours", "all", "any", "can", "had",
        "her", "was", "our", "out", "has", "have", "him", "his", "how", "man", "new", "old", "see",
        "two", "way", "who", "did", "let", "put", "say", "she", "too", "use", "that", "this", "with",
        "from", "they", "them", "then", "than", "there", "their", "what", "when", "where", "which",
        "will", "would", "could", "should", "been", "being", "were", "into", "onto", "over", "about",
        "after", "again", "also", "because", "before", "both", "each", "here", "more", "most", "much",
        "only", "other", "some", "such", "very", "why", "off", "own", "same", "these", "those",
        "through", "under", "until", "while", "does", "doing", "himself", "herself", "itself",
        "myself", "ourselves", "themselves", "whom", "whose", "don", "still", "even", "well",
        "back", "know", "think", "make", "made", "want", "need",
    }
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def vod_session_id(video_id: str) -> str:
    return f"vod:{video_id}"


def choose_bucket_seconds(duration_seconds: int, max_buckets: int) -> int:
    """Smallest standard bucket size that keeps the bucket count within ``max_buckets``."""
    duration = max(int(duration_seconds), 0)
    limit = max(int(max_buckets), 1)
    for choice in BUCKET_CHOICES:
        if math.ceil(duration / choice) <= limit:
            return choice
    return BUCKET_CHOICES[-1]


@dataclass(frozen=True)
class PeakCandidate:
    index: int
    start_index: int
    end_index: int  # exclusive
    peak_count: int
    baseline: float
    score: float


def moving_average(values: Sequence[float], window: int = 3) -> list[float]:
    """Centered moving average, truncated at the edges."""
    half = window // 2
    n = len(values)
    out: list[float] = []
    for i in range(n):
        chunk = values[max(0, i - half) : min(n, i + half + 1)]
        out.append(sum(chunk) / len(chunk))
    return out


def rolling_median(values: Sequence[float], window: int) -> list[float]:
    half = window // 2
    n = len(values)
    return [statistics.median(values[max(0, i - half) : min(n, i + half + 1)]) for i in range(n)]


def rolling_mad(values: Sequence[float], medians: Sequence[float], window: int) -> list[float]:
    """Median absolute deviation around the rolling median, same window."""
    half = window // 2
    n = len(values)
    out: list[float] = []
    for i in range(n):
        center = medians[i]
        chunk = values[max(0, i - half) : min(n, i + half + 1)]
        out.append(statistics.median(abs(value - center) for value in chunk))
    return out


def detect_peaks(
    counts: Sequence[int],
    bucket_seconds: int,
    *,
    max_peaks: int = 12,
    min_score: float = 3.0,
    min_gap_seconds: int = 90,
    baseline_window_seconds: int = 600,
) -> list[PeakCandidate]:
    """Find chat-activity peaks with a robust rolling z-score.

    Returns candidates sorted by bucket index. ``index`` is the raw-count maximum
    inside the detected extent (the smoothed maximum can sit one bucket off on
    plateaus produced by the 3-point moving average).
    """
    n = len(counts)
    if n < 5 or max(counts) <= 0 or max_peaks <= 0:
        return []
    b = max(int(bucket_seconds), 1)

    smoothed = moving_average([float(value) for value in counts], 3)
    window = max(15, round(baseline_window_seconds / b)) | 1
    base = rolling_median(smoothed, window)
    mad = rolling_mad(smoothed, base, window)
    z: list[float] = []
    for i in range(n):
        sigma = max(1.4826 * mad[i], math.sqrt(max(base[i], 0.0)), 1.0)
        z.append((smoothed[i] - base[i]) / sigma)

    candidates: list[int] = []
    for i in range(n):
        if z[i] < min_score or smoothed[i] < max(3.0, 1.5 * base[i]):
            continue
        left_ok = i == 0 or smoothed[i] >= smoothed[i - 1]
        right_ok = i == n - 1 or smoothed[i] > smoothed[i + 1]
        if left_ok and right_ok:
            candidates.append(i)

    gap = max(2, round(min_gap_seconds / b))
    accepted: list[int] = []
    for i in sorted(candidates, key=lambda idx: (-z[idx], idx)):
        if all(abs(i - other) >= gap for other in accepted):
            accepted.append(i)
            if len(accepted) >= max_peaks:
                break
    accepted.sort()
    accepted_set = set(accepted)

    max_extent = round(120 / b)
    results: list[PeakCandidate] = []
    previous_end = 0
    for i in accepted:
        level = base[i] + 0.35 * (smoothed[i] - base[i])
        start = i
        steps = 0
        while steps < max_extent and start - 1 >= 0 and start - 1 not in accepted_set and smoothed[start - 1] >= level:
            start -= 1
            steps += 1
        end = i + 1
        steps = 0
        while steps < max_extent and end < n and end not in accepted_set and smoothed[end] >= level:
            end += 1
            steps += 1
        start = max(start, previous_end)
        previous_end = end
        window_counts = counts[start:end]
        peak_count = max(window_counts)
        index = start + list(window_counts).index(peak_count)
        results.append(
            PeakCandidate(
                index=index,
                start_index=start,
                end_index=end,
                peak_count=int(peak_count),
                baseline=float(base[i]),
                score=float(z[i]),
            )
        )
    return results


def _format_rate(messages_per_second: float) -> str:
    if messages_per_second < 10:
        return f"{messages_per_second:.1f} msg/s"
    return f"{round(messages_per_second)} msg/s"


def build_peak_label(
    top_emotes: Sequence[TopItem],
    top_tokens: Sequence[TopItem],
    messages_per_second: float,
    *,
    max_terms: int = 2,
) -> str:
    """Heuristic label such as ``"KEKW · LUL · 42 msg/s"``."""
    terms: list[str] = []
    seen: set[str] = set()
    for item in top_emotes:
        if len(terms) >= max_terms:
            break
        value = item.value.strip()
        if item.count >= 2 and value and value.lower() not in seen:
            terms.append(value)
            seen.add(value.lower())
    emote_names = {item.value.strip().lower() for item in top_emotes}
    for item in top_tokens:
        if len(terms) >= max_terms:
            break
        value = item.value.strip()
        lowered = value.lower()
        if len(value) < 3 or lowered.isnumeric() or _is_number(lowered):
            continue
        if lowered in STOPWORDS or lowered in emote_names or lowered in seen:
            continue
        terms.append(value)
        seen.add(lowered)
    return " · ".join([*terms, _format_rate(messages_per_second)])


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def format_offset(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Collaborator protocols (implemented by WS1 / WS2; tests use fakes)
# ---------------------------------------------------------------------------


class VodGqlClient(Protocol):
    async def fetch_video(self, video_id: str) -> VodMetadata: ...

    def iter_comment_pages(self, video_id: str) -> AsyncIterator[list[dict]]: ...


GqlFactory = Callable[[], AbstractAsyncContextManager[Any]]
NormalizeFn = Callable[[dict, VodMetadata], ChatMessage | None]
SleepFn = Callable[[float], Awaitable[Any]]


class _VodFailure(Exception):
    """Expected pipeline failure with a user-facing message."""


def _default_normalize(node: dict, video: VodMetadata) -> ChatMessage | None:
    # TODO(integration): WS2 provides normalize_comment in app.ingestion.vod_replay.
    from app.ingestion.vod_replay import normalize_comment

    return normalize_comment(node, video)


# ---------------------------------------------------------------------------
# Analysis pipeline
# ---------------------------------------------------------------------------


class VodAnalysisService:
    def __init__(
        self,
        *,
        clickhouse: Any,
        kafka: Any,
        gql_factory: GqlFactory,
        settings: Settings,
        jobs: dict[str, VodAnalysisJob],
        normalize: NormalizeFn | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._clickhouse = clickhouse
        self._kafka = kafka
        self._gql_factory = gql_factory
        self._settings = settings
        self._jobs = jobs
        self._normalize = normalize or _default_normalize
        self._sleep = sleep or asyncio.sleep

    # -- job bookkeeping ----------------------------------------------------

    def _update(self, video_id: str, **changes: Any) -> VodAnalysisJob:
        now = datetime.now(UTC)
        current = self._jobs.get(video_id)
        if current is None:
            current = VodAnalysisJob(video_id=video_id, status="queued", started_at=now, updated_at=now)
        updated = current.model_copy(update={**changes, "updated_at": now})
        self._jobs[video_id] = updated
        return updated

    async def _flush(self) -> None:
        result = self._kafka.flush()
        if inspect.isawaitable(result):
            await result

    # -- entry point --------------------------------------------------------

    async def run(self, video_id: str, *, skip_fetch: bool = False) -> None:
        """Run the whole pipeline for one VOD. Never raises (except on cancellation)."""
        video: VodMetadata | None = None
        try:
            self._update(video_id, status="fetching", detail="Loading VOD metadata", error="")
            async with self._gql_factory() as gql:
                try:
                    video = await gql.fetch_video(video_id)
                except ValueError as exc:
                    raise _VodFailure("VOD not found or unavailable") from exc
                self._update(video_id, duration_seconds=video.duration_seconds)
                fetched = 0
                if not skip_fetch:
                    fetched = await self._fetch_and_publish(gql, video)

            session_id = vod_session_id(video_id)
            stored = await self._wait_for_catchup(video_id, session_id, expected=None if skip_fetch else fetched)

            self._update(video_id, status="analyzing", detail="Detecting chat peaks")
            analysis = await self._analyze(video)
            await self._clickhouse.upsert_vod_analysis(analysis)
            self._update(
                video_id,
                status="completed",
                stored_comments=stored,
                detail=f"{len(analysis.peaks)} peaks from {analysis.message_count} messages",
            )
        except asyncio.CancelledError:
            self._update(video_id, status="failed", error="Cancelled", detail="Job cancelled")
            raise
        except _VodFailure as exc:
            logger.warning("VOD analysis for %s failed: %s", video_id, exc)
            await self._fail(video_id, video, str(exc))
        except Exception as exc:
            # Full detail stays in server logs; the job and stored row get a sanitized error
            # (same precedent as timed transcription jobs).
            logger.exception("VOD analysis for %s failed", video_id)
            await self._fail(video_id, video, type(exc).__name__)

    async def _fail(self, video_id: str, video: VodMetadata | None, error: str) -> None:
        self._update(video_id, status="failed", error=error, detail="Analysis failed")
        if video is None:
            return
        now = datetime.now(UTC)
        failed = VodAnalysis(
            video_id=video.video_id,
            channel_id=video.channel_id,
            channel_login=video.channel_login,
            channel_display_name=video.channel_display_name,
            title=video.title,
            video_created_at=video.created_at,
            duration_seconds=video.duration_seconds,
            bucket_seconds=choose_bucket_seconds(video.duration_seconds, self._settings.vod_max_buckets),
            message_count=0,
            unique_chatter_count=0,
            status="failed",
            error=error,
            analyzed_at=now,
            updated_at=now,
        )
        try:
            await self._clickhouse.upsert_vod_analysis(failed)
        except Exception:
            logger.exception("Failed to store failed VOD analysis row for %s", video_id)

    # -- stage 1: fetch + publish ---------------------------------------------

    async def _fetch_and_publish(self, gql: VodGqlClient, video: VodMetadata) -> int:
        video_id = video.video_id
        max_comments = self._settings.vod_max_comments
        pages = 0
        fetched = 0
        last_offset = 0
        self._update(video_id, detail="Fetching chat replay")
        async for page in gql.iter_comment_pages(video_id):
            pages += 1
            for node in page:
                message = self._normalize(node, video)
                if message is None:
                    continue
                await self._kafka.publish(message)
                CHAT_MESSAGES_INGESTED.labels(source=VOD_METRIC_SOURCE, channel=message.channel_login).inc()
                fetched += 1
                offset = int((message.event_ts - video.created_at).total_seconds())
                last_offset = max(last_offset, offset)
            self._update(
                video_id,
                pages_fetched=pages,
                fetched_comments=fetched,
                last_offset_seconds=last_offset,
            )
            if fetched > max_comments:
                await self._flush()
                raise _VodFailure(f"VOD has more than {max_comments} chat comments")
            if pages % FLUSH_EVERY_PAGES == 0:
                await self._flush()
        await self._flush()
        return fetched

    # -- stage 2: wait for the ClickHouse consumer ----------------------------

    async def _wait_for_catchup(self, video_id: str, session_id: str, *, expected: int | None) -> int:
        settings = self._settings
        self._update(video_id, status="ingesting", detail="Waiting for ClickHouse consumer")
        if expected is None:
            stored = await self._clickhouse.vod_message_count(session_id)
            self._update(video_id, stored_comments=stored)
            return stored

        poll_seconds = max(settings.vod_catchup_poll_seconds, 0.0)
        timeout = settings.vod_catchup_timeout_seconds
        started = time.monotonic()
        waited = 0.0
        last: int | None = None
        stable = 0
        while True:
            stored = await self._clickhouse.vod_message_count(session_id)
            self._update(video_id, stored_comments=stored)
            if stored >= expected:
                return stored
            if stored == last and stored > 0:
                stable += 1
            else:
                stable = 0
            last = stored
            if stable >= settings.vod_catchup_stable_polls:
                # ReplacingMergeTree dedup (or dropped duplicates) can leave fewer rows than
                # fetched comments; accept once the count stops moving.
                return stored
            if waited >= timeout or time.monotonic() - started >= timeout:
                raise _VodFailure(f"ClickHouse consumer did not catch up (stored {stored} of {expected})")
            await self._sleep(poll_seconds)
            waited += poll_seconds

    # -- stage 3: analyze ----------------------------------------------------

    async def _analyze(self, video: VodMetadata) -> VodAnalysis:
        settings = self._settings
        session_id = vod_session_id(video.video_id)
        duration = max(video.duration_seconds, 0)
        bucket = choose_bucket_seconds(duration, settings.vod_max_buckets)
        buckets = await self._clickhouse.vod_activity(
            session_id=session_id,
            created_at=video.created_at,
            duration_seconds=duration,
            bucket_seconds=bucket,
        )
        counts = [item.message_count for item in buckets]
        candidates = detect_peaks(counts, bucket, max_peaks=settings.vod_max_peaks)

        peaks: list[VodPeak] = []
        for peak_id, candidate in enumerate(candidates, start=1):
            start_s = candidate.start_index * bucket
            end_s = candidate.end_index * bucket
            if duration > 0:
                end_s = max(min(end_s, duration), start_s + 1)
            count, _unique, emotes, tokens, samples = await self._clickhouse.vod_peak_context(
                session_id=session_id,
                window_start=video.created_at + timedelta(seconds=start_s),
                window_end=video.created_at + timedelta(seconds=end_s),
            )
            mps = candidate.peak_count / bucket
            peaks.append(
                VodPeak(
                    peak_id=peak_id,
                    start_seconds=start_s,
                    end_seconds=end_s,
                    peak_seconds=candidate.index * bucket,
                    message_count=count,
                    peak_bucket_count=candidate.peak_count,
                    messages_per_second=round(mps, 2),
                    score=round(candidate.score, 2),
                    baseline=round(candidate.baseline, 2),
                    top_emotes=list(emotes),
                    top_tokens=list(tokens),
                    sample_messages=list(samples)[:MAX_SAMPLE_MESSAGES],
                    label=build_peak_label(emotes, tokens, mps),
                )
            )

        total_count, total_unique, *_ = await self._clickhouse.vod_peak_context(
            session_id=session_id,
            window_start=video.created_at,
            window_end=video.created_at + timedelta(seconds=duration),
            top_limit=1,
            token_limit=1,
            sample_limit=1,
        )
        now = datetime.now(UTC)
        return VodAnalysis(
            video_id=video.video_id,
            channel_id=video.channel_id,
            channel_login=video.channel_login,
            channel_display_name=video.channel_display_name,
            title=video.title,
            video_created_at=video.created_at,
            duration_seconds=duration,
            bucket_seconds=bucket,
            message_count=total_count,
            unique_chatter_count=total_unique,
            status="completed",
            peaks=peaks,
            analyzed_at=now,
            updated_at=now,
        )


# ---------------------------------------------------------------------------
# Optional AI peak titles
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)
MAX_TITLE_LENGTH = 80


def _parse_titles(text: str, valid_ids: set[int] | None = None) -> dict[int, str]:
    """Parse ``{"titles": [{"peak_id": 1, "title": "..."}]}``; returns {} on garbage."""
    if not text:
        return {}
    body = text.strip()
    match = _FENCE_RE.match(body)
    if match:
        body = match.group(1).strip()
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(body[start : end + 1])
        except ValueError:
            return {}
    if not isinstance(payload, dict) or not isinstance(payload.get("titles"), list):
        return {}
    titles: dict[int, str] = {}
    for item in payload["titles"]:
        if not isinstance(item, dict):
            continue
        peak_id = item.get("peak_id")
        title = item.get("title")
        if isinstance(peak_id, bool) or not isinstance(title, str):
            continue
        if isinstance(peak_id, str) and peak_id.strip().isdigit():
            peak_id = int(peak_id.strip())
        if not isinstance(peak_id, int):
            continue
        if valid_ids is not None and peak_id not in valid_ids:
            continue
        cleaned = " ".join(title.split())[:MAX_TITLE_LENGTH].strip()
        if cleaned:
            titles[peak_id] = cleaned
    return titles


class VodLabelService:
    def __init__(
        self,
        *,
        clickhouse: Any,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._clickhouse = clickhouse
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def label(self, video_id: str) -> VodAnalysis:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        analysis = await self._clickhouse.get_vod_analysis(video_id)
        if analysis is None or analysis.status != "completed":
            raise ValueError("No completed analysis found for that VOD")
        if not analysis.peaks:
            raise ValueError("The analysis has no peaks to label")

        text = await asyncio.to_thread(self._call_openai, analysis)
        titles = _parse_titles(text, {peak.peak_id for peak in analysis.peaks})
        if not titles:
            logger.warning("OpenAI returned no usable peak titles for VOD %s", video_id)
            return analysis

        peaks = [
            peak.model_copy(update={"title": titles[peak.peak_id]}) if peak.peak_id in titles else peak
            for peak in analysis.peaks
        ]
        updated = analysis.model_copy(
            update={"peaks": peaks, "label_model": self._model, "updated_at": datetime.now(UTC)}
        )
        await self._clickhouse.upsert_vod_analysis(updated)
        return updated

    def _call_openai(self, analysis: VodAnalysis) -> str:
        client = OpenAI(api_key=self._api_key, timeout=self._timeout_seconds)
        response = client.responses.create(
            model=self._model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You title chat-activity peaks from a Twitch VOD for a dashboard. "
                        "Be concise and concrete, and avoid inventing facts that are not supported by the provided chat data."
                    ),
                },
                {"role": "user", "content": self._prompt(analysis)},
            ],
        )
        return response.output_text

    def _prompt(self, analysis: VodAnalysis) -> str:
        sections: list[str] = []
        for peak in analysis.peaks:
            emotes = ", ".join(f"{item.value} ({item.count})" for item in peak.top_emotes) or "none"
            words = ", ".join(f"{item.value} ({item.count})" for item in peak.top_tokens) or "none"
            samples = "\n".join(f"  - {line}" for line in peak.sample_messages[:MAX_SAMPLE_MESSAGES]) or "  - (none)"
            sections.append(
                f"#{peak.peak_id} at {format_offset(peak.peak_seconds)}, "
                f"{peak.messages_per_second:g} msg/s\n"
                f"Emotes: {emotes}\n"
                f"Words: {words}\n"
                f"Sample messages:\n{samples}"
            )
        peaks_text = "\n\n".join(sections)
        return f"""
Give each chat peak below a short title (at most 6 words) describing what chat reacted to.
Base titles only on the emotes, words and messages shown; do not invent events, names or facts.

VOD: {analysis.title}
Channel: {analysis.channel_login}

{peaks_text}

Respond with JSON only, exactly in this shape:
{{"titles": [{{"peak_id": 1, "title": "..."}}]}}
""".strip()
