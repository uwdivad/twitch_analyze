# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Twitch Analyze is a realtime Twitch chat analytics stack used as a personal project for practicing large-scale streamed-data handling (Kafka + ClickHouse), not a typical CRUD app. Backend: FastAPI/Pydantic/async Python. Frontend: React + TypeScript + Vite. See `AGENTS.md` for repo-guideline conventions (module layout, naming, PR/commit style) — read it alongside this file.

## Commands

```bash
# Local stack (Docker)
cp .env.example .env
docker compose up --build
docker compose config --quiet        # validate compose syntax

# Script-based startup (no Docker for backend/frontend/worker)
.venv/bin/python scripts/start.py infra   # Kafka, ClickHouse, Kafka Console only
.venv/bin/python scripts/start.py all     # infra + backend + worker + frontend

# Backend tests
cd backend && PYTHONPATH=. pytest
cd backend && PYTHONPATH=. pytest tests/test_summaries.py -k some_test  # single test

# Frontend
cd frontend && npm run dev      # Vite dev server
cd frontend && npm run build    # tsc typecheck + build (this is the frontend "test")
```

Local URLs: dashboard `http://localhost:5173`, API docs `http://localhost:8000/docs`, Kafka Console `http://localhost:8080`, Grafana `http://localhost:3000`.

`TWITCH_CHANNELS` in `.env` controls which channels are ingested; IRC mode (the default) can read public chat without Twitch credentials.

## Architecture

Core pipeline (required, not optional — do not design around bypassing Kafka):

```
Twitch IRC (default) or EventSub  ->  FastAPI ingest (backend/app/main.py lifespan)
  -> Kafka topic twitch.chat.messages (key: channel_id:session_id)
  -> ClickHouseConsumerWorker (backend/app/workers/clickhouse_consumer.py, separate process)
  -> ClickHouse chat_messages table
  -> FastAPI query API (backend/app/api/routes.py) -> React dashboard
```

In parallel, ingestion also broadcasts each message directly to `RealtimeHub` (backend/app/storage/realtime.py), which fans out over an `/api/messages/stream` SSE endpoint to the frontend's live feed — this path is independent of Kafka/ClickHouse so the live feed has lower latency, while ClickHouse remains the durable/historical source.

Key backend modules:
- `app/ingestion/irc.py`, `app/ingestion/twitch.py` — Twitch IRC and EventSub clients; normalize raw events into `ChatMessage` (`app/models/chat.py`) while preserving the raw payload in `raw_event`.
- `app/storage/kafka.py` — `KafkaJsonProducer` base + `KafkaChatProducer`; also reused for transcript segments.
- `app/storage/clickhouse.py` — `ClickHouseRepository`, the only ClickHouse access point. **All queries are serialized through a shared client** because `clickhouse-connect` sessions don't support concurrent queries — don't add a second concurrent ClickHouse client.
- `app/storage/realtime.py` — `RealtimeHub`, in-memory recent-message buffer + per-subscriber `asyncio.Queue` fanout consumed by the SSE endpoint.
- `app/workers/clickhouse_consumer.py`, `app/workers/transcript_consumer.py` — standalone Kafka consumer processes that batch-insert into ClickHouse and commit offsets only after a successful insert. Run as separate processes/containers, not inside the FastAPI app.
- `app/workers/audio_capture.py` — optional streamlink/ffmpeg audio capture + OpenAI transcription, feeds the transcript Kafka topic.
- `app/services/summaries.py` — `SummaryService.generate()`: builds a bounded context from `ClickHouseRepository.summary_context()` (counts, top chatters/emotes, sampled messages, spike windows — never the full raw history) and calls OpenAI to produce a Markdown chat summary, persisted to `chat_summaries`. See `docs/llm-summary-flow.md` for the full prompt/storage contract before touching this path.
- `app/ingestion/vod_replay.py` — Twitch VOD chat-replay fetcher over Twitch's undocumented web GQL API (`TwitchGqlClient`, `parse_vod_reference`, `normalize_comment`). Comments become `ChatMessage`s with `source='vod'`, `session_id='vod:<video_id>'` and `event_ts` = original air time. GQL URL, client id and persisted-query hash are settings (`TWITCH_GQL_*`).
- `app/services/vod_analysis.py` — `VodAnalysisService.run()`: fetch → publish to the normal Kafka chat topic → `flush()` → wait for the ClickHouse consumer to catch up → bucket activity → peak detection (rolling-median/MAD z-score, extents, heuristic keyword labels) → `vod_analyses`. `VodLabelService` optionally adds OpenAI peak titles. Jobs run as asyncio tasks in the API process (`app.state.vod_jobs`/`vod_tasks`).
- `/api/vods*` routes (`routes.py`): `POST /api/vods/analyze` (202; returns the stored completed analysis with no job unless `force`), `GET /api/vods`, `GET /api/vods/{id}`, `GET /api/vods/{id}/activity`, `POST /api/vods/{id}/label`.
- `app/core/config.py` — single `Settings` (pydantic-settings) object via `get_settings()`, reads `.env`.

**Live vs VOD rows:** `chat_messages.source` is `'live'` or `'vod'`. Every live dashboard query must keep filtering `source='live'` (`_message_filters(source="live")` in `clickhouse.py`) or VOD imports will inflate live analytics; VOD queries filter by `session_id` + `source='vod'`. `ensure_vod_schema()` migrates existing volumes (column, TTL on `received_at`, `vod_analyses`) and runs at startup in both the API and the consumer worker.

Dashboard endpoints fail soft by design: `/api/messages/recent` falls back to the in-memory `RealtimeHub` buffer if ClickHouse fails, and analytics endpoints return empty lists/zero counts on ClickHouse errors rather than raising. Preserve this pattern when adding endpoints.

ClickHouse schema (`sql/init-clickhouse.sql`): `chat_messages` is append-only, `ReplacingMergeTree`, partitioned by month, ordered by `(channel_id, session_id, event_ts, message_id)`. Other tables (`channels`, `stream_sessions`, `chat_interval_stats`, `chat_summaries`) follow the same append-heavy/analytical shape — see `docs/architecture-notes.md` for the full data-modeling rationale (why ClickHouse, why Kafka is mandatory, session-bucketing fallback rules).

Frontend (`frontend/src`): `App.tsx` is the dashboard root; `api/client.ts` wraps REST calls; `hooks/useLiveMessages.ts` consumes the SSE stream via `EventSource` and queues/flushes live messages at a user-selected display cadence (decoupled from render rate to survive high-volume chat); `components/` holds the dashboard panels (`LiveFeed`, `SummaryPanel`, `TranscriptionPanel`, charts, etc.); `types.ts` mirrors backend Pydantic models. `App.tsx` switches between the Live dashboard and the VODs view via a segmented control persisted in `location.hash` (`#live` / `#vods`).
- `features/vod/` — the VOD view (`VodView`, memoized because App re-renders on every live flush): Twitch player embed (`VodPlayer`, needs a `localhost`/HTTPS hostname as embed `parent`), SVG activity bar with peak markers and playhead (`VodActivityBar`), peak list, and `useVodAnalysis` (job polling, sessionStorage restore of the open VOD). API calls live in `api/vods.ts`; `vod.css` is imported by `VodView.tsx` so it loads after the global styles.
- `styles/` — `tokens.css` (design tokens for light/dark), `base.css`, `components.css` (shared primitives used by `components/ui/*`), `layout.css`; imported in that order in `main.tsx`. `hooks/useTheme.ts` owns the theme (system preference until the user toggles, then localStorage; sets `data-theme` on `<html>`), and `hooks/useThemeColors.ts` exposes token colors to charts.

## Docs worth reading before larger changes

- `docs/architecture-notes.md` — design rationale: why Kafka is required on the main path, why ClickHouse, data/session modeling rules, deployment direction.
- `docs/project-flow.md` — Mermaid diagrams of the end-to-end flow and ClickHouse data shape.
- `docs/llm-summary-flow.md` — exact request/response/storage contract for the summary feature.
- `docs/change-log.md` — record notable behavior changes here when you make them (per `AGENTS.md`).
