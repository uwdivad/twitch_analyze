from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.models.chat import ChatMessage, TopItem
from app.models.vod import VodActivityBucket, VodAnalysis, VodMetadata, VodPeak
from app.services.vod_analysis import (
    VodAnalysisService,
    VodLabelService,
    _parse_titles,
    build_peak_label,
    choose_bucket_seconds,
    detect_peaks,
)

CREATED_AT = datetime(2026, 5, 1, 18, 0, tzinfo=UTC)


def spiky_counts() -> list[int]:
    counts = [10] * 600
    counts[100] = 60
    counts[101] = 45
    counts[300] = 80
    counts[450] = 13
    return counts


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duration", "max_buckets", "expected"),
    [
        (0, 720, 5),
        (3600, 720, 5),
        (3601, 720, 10),
        (7200, 720, 10),
        (10800, 720, 15),
        (21600, 720, 30),
        (43200, 720, 60),
        (86400, 720, 120),
        (200000, 720, 300),
        (10_000_000, 720, 300),
        (600, 100, 10),
    ],
)
def test_choose_bucket_seconds(duration: int, max_buckets: int, expected: int) -> None:
    assert choose_bucket_seconds(duration, max_buckets) == expected


@pytest.mark.parametrize("bucket_seconds", [5, 10])
def test_detect_peaks_finds_real_spikes_only(bucket_seconds: int) -> None:
    peaks = detect_peaks(spiky_counts(), bucket_seconds)

    assert [peak.index for peak in peaks] == [100, 300]
    for peak in peaks:
        assert peak.start_index <= peak.index < peak.end_index
        assert peak.score >= 3.0
        assert peak.baseline == pytest.approx(10.0)
    assert peaks[0].peak_count == 60
    assert peaks[1].peak_count == 80
    assert peaks[0].end_index <= peaks[1].start_index


def test_detect_peaks_flat_and_short_series_are_empty() -> None:
    assert detect_peaks([10] * 600, 10) == []
    assert detect_peaks([0] * 600, 10) == []
    assert detect_peaks([0, 50, 0, 0], 10) == []


def test_detect_peaks_merges_adjacent_spikes() -> None:
    counts = [10] * 300
    counts[150] = 50
    counts[151] = 50
    counts[152] = 40

    peaks = detect_peaks(counts, 10)

    assert len(peaks) == 1
    assert peaks[0].start_index <= 150 < peaks[0].end_index
    assert peaks[0].index == 150


def test_detect_peaks_respects_max_peaks_and_orders_by_index() -> None:
    counts = [10] * 600
    for index, value in ((50, 40), (200, 90), (350, 60), (500, 70)):
        counts[index] = value

    peaks = detect_peaks(counts, 10, max_peaks=2)

    assert [peak.index for peak in peaks] == [200, 500]


def test_build_peak_label_prefers_emotes() -> None:
    label = build_peak_label(
        [TopItem(value="KEKW", count=30), TopItem(value="LUL", count=12), TopItem(value="OMEGALUL", count=5)],
        [TopItem(value="clutch", count=9)],
        42.0,
    )
    assert label == "KEKW · LUL · 42 msg/s"


def test_build_peak_label_skips_stopwords_numbers_and_emote_tokens() -> None:
    label = build_peak_label(
        [TopItem(value="PogChamp", count=1), TopItem(value="KEKW", count=4)],
        [
            TopItem(value="the", count=50),
            TopItem(value="LOL", count=40),
            TopItem(value="kekw", count=30),
            TopItem(value="123", count=20),
            TopItem(value="gg", count=15),
            TopItem(value="clutch", count=10),
            TopItem(value="baron", count=8),
        ],
        7.5,
    )
    assert label == "KEKW · clutch · 7.5 msg/s"


def test_build_peak_label_rate_only_fallback() -> None:
    assert build_peak_label([], [TopItem(value="and", count=5)], 3.4) == "3.4 msg/s"
    assert build_peak_label([], [], 12.4) == "12 msg/s"
    assert build_peak_label([], [], 0.4) == "0.4 msg/s"


# ---------------------------------------------------------------------------
# VodAnalysisService
# ---------------------------------------------------------------------------


class RecordingJobs(dict):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[str] = []

    def __setitem__(self, key, value) -> None:
        if not self.statuses or self.statuses[-1] != value.status:
            self.statuses.append(value.status)
        super().__setitem__(key, value)


def metadata(duration_seconds: int = 3000) -> VodMetadata:
    return VodMetadata(
        video_id="123456",
        title="Big stream",
        duration_seconds=duration_seconds,
        created_at=CREATED_AT,
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
    )


class FakeGql:
    def __init__(self, pages: list[list[dict]], *, video: VodMetadata | None = None, error: Exception | None = None):
        self.pages = pages
        self.video = video or metadata()
        self.error = error
        self.entered = 0
        self.exited = 0
        self.pages_requested = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *_exc):
        self.exited += 1
        return False

    async def fetch_video(self, video_id: str) -> VodMetadata:
        if self.error is not None:
            raise self.error
        assert video_id == self.video.video_id
        return self.video

    async def iter_comment_pages(self, video_id: str):
        for page in self.pages:
            self.pages_requested += 1
            yield page


def fake_normalize(node: dict, video: VodMetadata) -> ChatMessage | None:
    if node.get("skip"):
        return None
    return ChatMessage(
        message_id=node["id"],
        channel_id=video.channel_id,
        channel_login=video.channel_login,
        channel_display_name=video.channel_display_name,
        session_id=f"vod:{video.video_id}",
        session_date=date(2026, 5, 1),
        chatter_user_id="user-1",
        chatter_login="viewer",
        chatter_display_name="Viewer",
        message_text=node.get("text", "hi"),
        event_ts=video.created_at + timedelta(seconds=node["offset"]),
        source="vod",
    )


class FakeKafka:
    def __init__(self) -> None:
        self.published: list[ChatMessage] = []
        self.flushes = 0

    async def publish(self, message: ChatMessage) -> None:
        self.published.append(message)

    async def flush(self) -> None:
        self.flushes += 1


class FakeClickHouse:
    def __init__(self, kafka: FakeKafka, *, counts: list[int] | None = None, stored: int | None = None):
        self.kafka = kafka
        self.counts = counts if counts is not None else spiky_counts()
        self.stored = stored
        self.activity_calls: list[dict] = []
        self.context_calls: list[dict] = []
        self.upserted: list[VodAnalysis] = []
        self.count_calls = 0
        self.fail_activity = False

    async def vod_message_count(self, session_id: str) -> int:
        assert session_id == "vod:123456"
        self.count_calls += 1
        if self.stored is not None:
            return self.stored
        return len(self.kafka.published)

    async def vod_activity(self, *, session_id, created_at, duration_seconds, bucket_seconds):
        self.activity_calls.append(
            {"session_id": session_id, "created_at": created_at, "duration": duration_seconds, "bucket": bucket_seconds}
        )
        if self.fail_activity:
            raise RuntimeError("secret connection detail")
        return [
            VodActivityBucket(index=i, offset_seconds=i * bucket_seconds, message_count=count, unique_chatter_count=count)
            for i, count in enumerate(self.counts)
        ]

    async def vod_peak_context(self, *, session_id, window_start, window_end, top_limit=5, token_limit=40, sample_limit=20):
        self.context_calls.append({"window_start": window_start, "window_end": window_end})
        if window_start == CREATED_AT and window_end == CREATED_AT + timedelta(seconds=3000):
            return 6100, 900, [], [], []
        samples = [f"viewer{i}: KEKW" for i in range(30)]
        return 150, 40, [TopItem(value="KEKW", count=25)], [TopItem(value="clutch", count=6)], samples

    async def upsert_vod_analysis(self, analysis: VodAnalysis) -> None:
        self.upserted.append(analysis)


def service_settings(**overrides) -> Settings:
    values = {
        "vod_catchup_poll_seconds": 0.0,
        "vod_catchup_timeout_seconds": 5.0,
        "vod_catchup_stable_polls": 3,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


async def no_sleep(_seconds: float) -> None:
    return None


def build_service(gql: FakeGql, kafka: FakeKafka, clickhouse: FakeClickHouse, jobs: dict, **settings_overrides):
    return VodAnalysisService(
        clickhouse=clickhouse,
        kafka=kafka,
        gql_factory=lambda: gql,
        settings=service_settings(**settings_overrides),
        jobs=jobs,
        normalize=fake_normalize,
        sleep=no_sleep,
    )


def pages_of(count: int, per_page: int = 1) -> list[list[dict]]:
    pages: list[list[dict]] = []
    n = 0
    for _ in range(count):
        page = []
        for _ in range(per_page):
            page.append({"id": f"c{n}", "offset": n})
            n += 1
        pages.append(page)
    return pages


@pytest.mark.anyio
async def test_run_fetches_publishes_and_stores_peaks() -> None:
    pages = pages_of(120)
    pages[0].append({"id": "skipped", "offset": 0, "skip": True})
    gql = FakeGql(pages)
    kafka = FakeKafka()
    clickhouse = FakeClickHouse(kafka)
    jobs = RecordingJobs()

    await build_service(gql, kafka, clickhouse, jobs).run("123456")

    assert jobs.statuses == ["fetching", "ingesting", "analyzing", "completed"]
    job = jobs["123456"]
    assert job.pages_fetched == 120
    assert job.fetched_comments == 120
    assert job.stored_comments == 120
    assert job.last_offset_seconds == 119
    assert job.duration_seconds == 3000
    assert job.error == ""
    assert len(kafka.published) == 120
    assert kafka.flushes == 3  # every 50 pages + final flush
    assert gql.entered == gql.exited == 1

    assert clickhouse.activity_calls == [
        {"session_id": "vod:123456", "created_at": CREATED_AT, "duration": 3000, "bucket": 5}
    ]
    assert len(clickhouse.upserted) == 1
    analysis = clickhouse.upserted[0]
    assert analysis.status == "completed"
    assert analysis.bucket_seconds == 5
    assert analysis.message_count == 6100
    assert analysis.unique_chatter_count == 900
    assert [peak.peak_id for peak in analysis.peaks] == [1, 2]
    assert [peak.peak_seconds for peak in analysis.peaks] == [500, 1500]
    first = analysis.peaks[0]
    assert first.start_seconds <= first.peak_seconds < first.end_seconds
    assert first.peak_bucket_count == 60
    assert first.messages_per_second == 12.0
    assert first.message_count == 150
    assert len(first.sample_messages) == 20
    assert first.label == "KEKW · clutch · 12 msg/s"
    assert job.detail == "2 peaks from 6100 messages"
    # Per-peak windows are offsets from the VOD creation time.
    assert clickhouse.context_calls[0]["window_start"] == CREATED_AT + timedelta(seconds=first.start_seconds)


@pytest.mark.anyio
async def test_run_skip_fetch_publishes_nothing() -> None:
    gql = FakeGql(pages_of(5))
    kafka = FakeKafka()
    clickhouse = FakeClickHouse(kafka, stored=4200)
    jobs = RecordingJobs()

    await build_service(gql, kafka, clickhouse, jobs).run("123456", skip_fetch=True)

    assert kafka.published == []
    assert kafka.flushes == 0
    assert gql.pages_requested == 0
    assert jobs.statuses == ["fetching", "ingesting", "analyzing", "completed"]
    assert jobs["123456"].stored_comments == 4200
    assert clickhouse.count_calls == 1
    assert clickhouse.upserted[0].status == "completed"


@pytest.mark.anyio
async def test_run_fails_when_consumer_does_not_catch_up() -> None:
    gql = FakeGql(pages_of(3))
    kafka = FakeKafka()
    clickhouse = FakeClickHouse(kafka, stored=0)
    jobs = RecordingJobs()

    await build_service(
        gql, kafka, clickhouse, jobs, vod_catchup_poll_seconds=0.5, vod_catchup_timeout_seconds=1.0
    ).run("123456")

    job = jobs["123456"]
    assert jobs.statuses == ["fetching", "ingesting", "failed"]
    assert job.error == "ClickHouse consumer did not catch up (stored 0 of 3)"
    assert clickhouse.activity_calls == []
    assert [row.status for row in clickhouse.upserted] == ["failed"]


@pytest.mark.anyio
async def test_run_accepts_stable_partial_count() -> None:
    gql = FakeGql(pages_of(10))
    kafka = FakeKafka()
    clickhouse = FakeClickHouse(kafka, stored=8)
    jobs = RecordingJobs()

    await build_service(gql, kafka, clickhouse, jobs, vod_catchup_stable_polls=2).run("123456")

    assert jobs["123456"].status == "completed"
    assert jobs["123456"].stored_comments == 8
    assert clickhouse.count_calls == 3


@pytest.mark.anyio
async def test_run_fails_over_comment_cap() -> None:
    gql = FakeGql(pages_of(4, per_page=3))
    kafka = FakeKafka()
    clickhouse = FakeClickHouse(kafka)
    jobs = RecordingJobs()

    await build_service(gql, kafka, clickhouse, jobs, vod_max_comments=5).run("123456")

    job = jobs["123456"]
    assert job.status == "failed"
    assert "more than 5" in job.error
    assert gql.pages_requested == 2
    assert kafka.flushes == 1


@pytest.mark.anyio
async def test_run_missing_vod_fails_cleanly() -> None:
    gql = FakeGql([], error=ValueError("no such video"))
    kafka = FakeKafka()
    clickhouse = FakeClickHouse(kafka)
    jobs = RecordingJobs()

    await build_service(gql, kafka, clickhouse, jobs).run("123456")

    assert jobs.statuses == ["fetching", "failed"]
    assert jobs["123456"].error == "VOD not found or unavailable"
    assert clickhouse.upserted == []  # no metadata -> no analysis row


@pytest.mark.anyio
async def test_run_unexpected_error_is_sanitized_and_stored() -> None:
    gql = FakeGql(pages_of(2))
    kafka = FakeKafka()
    clickhouse = FakeClickHouse(kafka)
    clickhouse.fail_activity = True
    jobs = RecordingJobs()

    await build_service(gql, kafka, clickhouse, jobs).run("123456")

    job = jobs["123456"]
    assert jobs.statuses == ["fetching", "ingesting", "analyzing", "failed"]
    assert job.error == "RuntimeError"
    assert "secret" not in job.detail
    assert len(clickhouse.upserted) == 1
    failed = clickhouse.upserted[0]
    assert failed.status == "failed"
    assert failed.error == "RuntimeError"
    assert failed.video_id == "123456"
    assert failed.peaks == []


# ---------------------------------------------------------------------------
# VodLabelService
# ---------------------------------------------------------------------------


def test_parse_titles_handles_fences_bad_ids_and_garbage() -> None:
    text = '```json\n{"titles": [{"peak_id": 1, "title": "  Clutch   baron steal "}, {"peak_id": 9, "title": "x"}, {"peak_id": "2", "title": "Chat loses it"}, {"peak_id": 3}]}\n```'
    assert _parse_titles(text, {1, 2, 3}) == {1: "Clutch baron steal", 2: "Chat loses it"}
    assert _parse_titles("not json at all") == {}
    assert _parse_titles('{"titles": "nope"}') == {}
    assert _parse_titles("") == {}
    assert _parse_titles('Sure! {"titles": [{"peak_id": 4, "title": "Ok"}]}') == {4: "Ok"}


def labeled_analysis(status: str = "completed", peaks: list[VodPeak] | None = None) -> VodAnalysis:
    if peaks is None:
        peaks = [
            VodPeak(
                peak_id=i,
                start_seconds=i * 100,
                end_seconds=i * 100 + 30,
                peak_seconds=i * 100 + 10,
                message_count=100,
                peak_bucket_count=40,
                messages_per_second=8.0,
                score=6.0,
                baseline=10.0,
                top_emotes=[TopItem(value="KEKW", count=20)],
                top_tokens=[TopItem(value="baron", count=5)],
                sample_messages=["viewer: KEKW baron"],
                label="KEKW · baron · 8.0 msg/s",
            )
            for i in (1, 2)
        ]
    return VodAnalysis(
        video_id="123456",
        channel_id="channel-1",
        channel_login="example",
        channel_display_name="Example",
        title="Big stream",
        video_created_at=CREATED_AT,
        duration_seconds=3000,
        bucket_seconds=5,
        message_count=6100,
        unique_chatter_count=900,
        status=status,
        peaks=peaks,
        analyzed_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


class LabelClickHouse:
    def __init__(self, analysis: VodAnalysis | None) -> None:
        self.analysis = analysis
        self.upserted: list[VodAnalysis] = []

    async def get_vod_analysis(self, video_id: str) -> VodAnalysis | None:
        return self.analysis

    async def upsert_vod_analysis(self, analysis: VodAnalysis) -> None:
        self.upserted.append(analysis)


@pytest.mark.anyio
async def test_label_rejects_missing_key_and_missing_analysis() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await VodLabelService(clickhouse=LabelClickHouse(labeled_analysis()), api_key="", model="m").label("123456")
    for analysis in (None, labeled_analysis(status="failed"), labeled_analysis(peaks=[])):
        with pytest.raises(ValueError):
            await VodLabelService(clickhouse=LabelClickHouse(analysis), api_key="k", model="m").label("123456")


@pytest.mark.anyio
async def test_label_writes_titles_with_fake_openai(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                output_text='```json\n{"titles": [{"peak_id": 1, "title": "Baron steal"}, {"peak_id": 7, "title": "Bogus"}]}\n```'
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setattr("app.services.vod_analysis.OpenAI", FakeOpenAI)
    clickhouse = LabelClickHouse(labeled_analysis())
    service = VodLabelService(clickhouse=clickhouse, api_key="test-key", model="label-model", timeout_seconds=12.5)

    result = await service.label("123456")

    assert captured["client"] == {"api_key": "test-key", "timeout": 12.5}
    assert captured["request"]["model"] == "label-model"
    prompt = captured["request"]["input"][1]["content"]
    assert "#1 at 0:01:50, 8 msg/s" in prompt
    assert "viewer: KEKW baron" in prompt
    assert result.label_model == "label-model"
    assert [peak.title for peak in result.peaks] == ["Baron steal", ""]
    assert [peak.label for peak in result.peaks] == ["KEKW · baron · 8.0 msg/s"] * 2
    assert clickhouse.upserted == [result]


@pytest.mark.anyio
async def test_label_keeps_heuristic_labels_on_garbage(monkeypatch) -> None:
    clickhouse = LabelClickHouse(labeled_analysis())
    service = VodLabelService(clickhouse=clickhouse, api_key="test-key", model="label-model")
    monkeypatch.setattr(service, "_call_openai", lambda _analysis: "I cannot do that")

    result = await service.label("123456")

    assert [peak.title for peak in result.peaks] == ["", ""]
    assert result.label_model == ""
    assert clickhouse.upserted == []
