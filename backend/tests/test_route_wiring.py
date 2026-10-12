"""Verify the auth / feature-flag guards are actually attached to the real routes.

The guard functions themselves are unit-tested elsewhere; these tests go through the
real router with a TestClient so a guard dropped from a route decorator fails here.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import Settings, get_settings
from app.services.vod_analysis import VodNotFoundError

TOKEN = "secret-token"


class FakeClickHouse:
    def __init__(self) -> None:
        self.inserted_messages: list[Any] = []
        self.inserted_summaries: list[Any] = []

    async def insert_messages(self, messages: list[Any]) -> None:
        self.inserted_messages.extend(messages)

    async def insert_summary(self, summary: Any) -> None:
        self.inserted_summaries.append(summary)


class FakeSummaryService:
    async def generate(self, **_kwargs: Any) -> Any:
        raise ValueError("handler reached")


class FakeLabelService:
    async def label(self, video_id: str) -> Any:
        raise VodNotFoundError("handler reached")


def _bad_reference(_value: str) -> str:
    raise ValueError("handler reached")


def _chat_message() -> dict[str, Any]:
    return {
        "message_id": "message-1",
        "channel_id": "channel-1",
        "channel_login": "example",
        "channel_display_name": "Example",
        "session_id": "channel-1:2026-04-28",
        "session_date": date(2026, 4, 28).isoformat(),
        "chatter_user_id": "user-1",
        "chatter_login": "viewer",
        "chatter_display_name": "Viewer",
        "message_text": "hello",
        "event_ts": datetime(2026, 4, 28, tzinfo=UTC).isoformat(),
    }


def _chat_summary() -> dict[str, Any]:
    return {
        "summary_id": "summary-1",
        "channel_id": "channel-1",
        "channel_login": "example",
        "session_id": "channel-1:2026-04-28",
        "window_start": datetime(2026, 4, 28, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 4, 28, 1, tzinfo=UTC).isoformat(),
        "window_size": "60m",
        "summary_text": "A short summary.",
        "model": "test-model",
        "source_stats": {"message_count": 1, "unique_chatter_count": 1},
        "created_at": datetime(2026, 4, 28, 1, tzinfo=UTC).isoformat(),
    }


# (method, path, json body, feature flag or None, status + detail proving the handler ran)
GUARDED_ROUTES: list[tuple[str, str, Any, str | None, int, str]] = [
    ("POST", "/api/summaries/generate", {"channel": "example"}, "enable_summaries", 404, "handler reached"),
    ("POST", "/api/summaries", _chat_summary(), None, 200, ""),
    ("POST", "/api/messages", [_chat_message()], None, 200, ""),
    (
        "POST",
        "/api/transcriptions/start",
        {"channel": "example", "duration_minutes": 1},
        "enable_transcription",
        503,
        "OPENAI_API_KEY is required",
    ),
    ("POST", "/api/vods/analyze", {"video": "123456"}, "enable_vod_analysis", 400, "Invalid Twitch VOD"),
    ("POST", "/api/vods/123456/label", None, "enable_vod_labels", 404, "handler reached"),
    ("GET", "/api/settings", None, None, 200, ""),
    ("PUT", "/api/settings", {"values": {"kafka_chat_topic": "x"}}, None, 422, "KAFKA_CHAT_TOPIC"),
]
ROUTE_IDS = [f"{method} {path}" for method, path, *_ in GUARDED_ROUTES]


@pytest.fixture
def make_client(tmp_path, monkeypatch) -> Callable[..., TestClient]:
    # Empty cwd: no developer .env or saved overrides leak into Settings() calls.
    monkeypatch.chdir(tmp_path)

    def factory(**overrides: Any) -> TestClient:
        settings = Settings(
            _env_file=None,
            settings_overrides_file=str(tmp_path / "overrides.json"),
            openai_api_key="",
            **overrides,
        )
        # Route handlers that call get_settings() directly (not via Depends).
        monkeypatch.setattr(routes, "get_settings", lambda: settings)
        app = FastAPI()
        app.include_router(routes.router)
        app.state.transcription_jobs = {}
        app.state.transcription_tasks = {}
        app.state.vod_jobs = {}
        app.state.vod_tasks = {}
        clickhouse = FakeClickHouse()
        # Keyed by the original function object that the routes' Depends() captured.
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[routes.get_clickhouse] = lambda: clickhouse
        app.dependency_overrides[routes.get_summary_service] = FakeSummaryService
        app.dependency_overrides[routes.get_vod_label_service] = FakeLabelService
        app.dependency_overrides[routes.get_vod_service] = lambda: object()
        app.dependency_overrides[routes.get_vod_reference_parser] = lambda: _bad_reference
        return TestClient(app)

    return factory


def _call(client: TestClient, method: str, path: str, body: Any, headers: dict[str, str] | None = None):
    return client.request(method, path, json=body, headers=headers or {})


@pytest.mark.parametrize(("method", "path", "body", "flag", "status", "detail"), GUARDED_ROUTES, ids=ROUTE_IDS)
def test_guarded_route_requires_api_key_when_token_set(make_client, method, path, body, flag, status, detail) -> None:
    client = make_client(api_auth_token=TOKEN)

    for headers in (None, {"X-API-Key": "wrong-token"}):
        response = _call(client, method, path, body, headers)
        assert response.status_code == 401, (headers, response.text)
        assert response.json()["detail"] == "Invalid or missing API key"

    response = _call(client, method, path, body, {"X-API-Key": TOKEN})
    assert response.status_code == status, response.text
    assert detail in response.text


@pytest.mark.parametrize(("method", "path", "body", "flag", "status", "detail"), GUARDED_ROUTES, ids=ROUTE_IDS)
def test_guarded_route_is_open_without_token(make_client, method, path, body, flag, status, detail) -> None:
    client = make_client(api_auth_token="")

    response = _call(client, method, path, body)

    assert response.status_code == status, response.text
    assert detail in response.text


FLAGGED_ROUTES = [route for route in GUARDED_ROUTES if route[3] is not None]


@pytest.mark.parametrize(
    ("method", "path", "body", "flag", "status", "detail"),
    FLAGGED_ROUTES,
    ids=[f"{method} {path}" for method, path, *_ in FLAGGED_ROUTES],
)
def test_flagged_route_returns_403_when_feature_off(make_client, method, path, body, flag, status, detail) -> None:
    client = make_client(api_auth_token=TOKEN, **{flag: False})

    response = _call(client, method, path, body, {"X-API-Key": TOKEN})

    assert response.status_code == 403, response.text
    assert response.json()["detail"].startswith("Feature turned off in Settings:")

    # Auth is still checked first-class: no key is a 401 even with the feature off.
    assert _call(client, method, path, body).status_code == 401


def test_every_mutating_route_requires_api_key() -> None:
    def calls(dependant: Any) -> set[Any]:
        found = {dependant.call}
        for sub in dependant.dependencies:
            found |= calls(sub)
        return found

    unguarded = [
        (sorted(route.methods), route.path)
        for route in routes.router.routes
        if isinstance(route, APIRoute)
        and (route.methods & {"POST", "PUT", "PATCH", "DELETE"} or route.path == "/api/settings")
        and routes.require_api_key not in calls(route.dependant)
    ]

    assert unguarded == []


def test_features_endpoint_reflects_disabled_flags(make_client) -> None:
    client = make_client(enable_summaries=False, enable_vod_labels=False, enable_audio_capture=True)

    response = client.get("/api/features")

    assert response.status_code == 200
    assert response.json() == {
        "twitch_ingestion": True,
        "summaries": False,
        "transcription": True,
        "vod_analysis": True,
        "vod_labels": False,
        "audio_capture": True,
    }
