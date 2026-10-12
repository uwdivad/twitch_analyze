import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import routes
from app.core.config import Settings
from app.models.clips import ClipJob, CreateClipRequest


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _request(jobs=None, tasks=None):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(clip_jobs=jobs if jobs is not None else {}, clip_tasks=tasks if tasks is not None else {})
        )
    )


def _job(job_id: str, status: str = "completed", started_at: datetime | None = None, **extra) -> ClipJob:
    now = started_at or datetime(2026, 10, 8, 19, 0, tzinfo=UTC)
    return ClipJob(
        job_id=job_id,
        platform="twitch",
        channel_login="xqc",
        requested_seconds=300,
        status=status,
        started_at=now,
        updated_at=now,
        **extra,
    )


class FakeClipService:
    def __init__(self, video_dir: Path | None = None):
        self.calls = []
        self.deleted = []
        self.video_dir = video_dir or Path("/nonexistent")

    async def run(self, job_id):
        self.calls.append(job_id)

    def video_path(self, job_id):
        return self.video_dir / f"{job_id}.mp4"

    def delete(self, job_id):
        self.deleted.append(job_id)


def _parse_ok(value: str) -> str:
    return value.lower()


def _parse_bad(value: str) -> str:
    raise ValueError("bad channel")


async def _create(request, payload, service, parse=_parse_ok, **settings):
    return await routes.create_clip(
        request=request,
        payload=payload,
        settings=_settings(**settings),
        service=service,
        parse_channel=parse,
    )


@pytest.mark.anyio
async def test_create_clip_registers_job_and_starts_task() -> None:
    request = _request()
    service = FakeClipService()

    job = await _create(request, CreateClipRequest(channel="xQc", duration_minutes=10), service)

    assert job.status == "queued"
    assert job.channel_login == "xqc"
    assert job.platform == "twitch"
    assert job.requested_seconds == 600
    assert request.app.state.clip_jobs == {job.job_id: job}
    assert job.job_id in request.app.state.clip_tasks
    await request.app.state.clip_tasks[job.job_id]
    await asyncio.sleep(0)
    assert service.calls == [job.job_id]
    assert request.app.state.clip_tasks == {}


@pytest.mark.anyio
async def test_create_clip_validation_and_limits() -> None:
    with pytest.raises(HTTPException) as exc:
        await _create(_request(), CreateClipRequest(channel="nonsense"), FakeClipService(), parse=_parse_bad)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await _create(_request(), CreateClipRequest(channel="xqc", duration_minutes=30), FakeClipService(), clip_max_duration_minutes=15)
    assert exc.value.status_code == 400

    request = _request(jobs={"running": _job("running", status="downloading"), "done": _job("done")})
    with pytest.raises(HTTPException) as exc:
        await _create(request, CreateClipRequest(channel="xqc"), FakeClipService(), clip_max_concurrent_jobs=1)
    assert exc.value.status_code == 429
    assert set(request.app.state.clip_jobs) == {"running", "done"}


def test_create_clip_request_rejects_unknown_platform_and_bad_durations() -> None:
    with pytest.raises(ValueError):
        CreateClipRequest(channel="xqc", platform="youtube")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CreateClipRequest(channel="xqc", duration_minutes=0)
    with pytest.raises(ValueError):
        CreateClipRequest(channel="xqc", duration_minutes=61)


@pytest.mark.anyio
async def test_clip_list_is_newest_first_and_detail_404s() -> None:
    older = _job("older", started_at=datetime(2026, 10, 8, 18, 0, tzinfo=UTC))
    newer = _job("newer", started_at=datetime(2026, 10, 8, 19, 0, tzinfo=UTC))
    request = _request(jobs={"older": older, "newer": newer})

    assert await routes.clips(request=request, limit=50) == [newer, older]
    assert await routes.clips(request=request, limit=1) == [newer]
    assert await routes.clip(request=request, job_id="older") == older
    with pytest.raises(HTTPException) as exc:
        await routes.clip(request=request, job_id="missing")
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_download_clip_serves_file_or_errors(tmp_path: Path) -> None:
    service = FakeClipService(video_dir=tmp_path)
    request = _request(jobs={"done": _job("done", file_name="xqc_clip.mp4"), "busy": _job("busy", status="downloading")})

    with pytest.raises(HTTPException) as exc:
        await routes.download_clip(request=request, job_id="missing", service=service)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await routes.download_clip(request=request, job_id="busy", service=service)
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as exc:
        await routes.download_clip(request=request, job_id="done", service=service)
    assert exc.value.status_code == 404

    (tmp_path / "done.mp4").write_bytes(b"video")
    response = await routes.download_clip(request=request, job_id="done", service=service)
    assert Path(response.path) == tmp_path / "done.mp4"
    assert response.media_type == "video/mp4"
    assert 'filename="xqc_clip.mp4"' in response.headers["content-disposition"]


@pytest.mark.anyio
async def test_delete_clip_cancels_running_task_and_removes_files() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _running():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(_running())
    await started.wait()
    request = _request(jobs={"busy": _job("busy", status="downloading")}, tasks={"busy": task})
    service = FakeClipService()

    response = await routes.delete_clip(request=request, job_id="busy", service=service)

    assert response.status_code == 204
    assert cancelled.is_set()
    assert request.app.state.clip_tasks == {}
    assert service.deleted == ["busy"]

    with pytest.raises(HTTPException) as exc:
        await routes.delete_clip(request=request, job_id="missing", service=service)
    assert exc.value.status_code == 404


def test_prune_finished_jobs_by_finished_statuses_keeps_completed_clips() -> None:
    jobs = {f"failed-{i}": _job(f"failed-{i}", status="failed") for i in range(5)}
    jobs["done"] = _job("done")
    jobs["busy"] = _job("busy", status="downloading")

    routes._prune_finished_jobs(jobs, 2, finished_statuses=frozenset({"failed"}))

    assert set(jobs) == {"failed-3", "failed-4", "done", "busy"}
