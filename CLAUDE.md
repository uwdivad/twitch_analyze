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
- `app/core/config.py` — single `Settings` (pydantic-settings) object via `get_settings()`, reads `.env`.

Dashboard endpoints fail soft by design: `/api/messages/recent` falls back to the in-memory `RealtimeHub` buffer if ClickHouse fails, and analytics endpoints return empty lists/zero counts on ClickHouse errors rather than raising. Preserve this pattern when adding endpoints.

ClickHouse schema (`sql/init-clickhouse.sql`): `chat_messages` is append-only, `ReplacingMergeTree`, partitioned by month, ordered by `(channel_id, session_id, event_ts, message_id)`. Other tables (`channels`, `stream_sessions`, `chat_interval_stats`, `chat_summaries`) follow the same append-heavy/analytical shape — see `docs/architecture-notes.md` for the full data-modeling rationale (why ClickHouse, why Kafka is mandatory, session-bucketing fallback rules).

Frontend (`frontend/src`): `App.tsx` is the dashboard root; `api/client.ts` wraps REST calls; `hooks/useLiveMessages.ts` consumes the SSE stream via `EventSource` and queues/flushes live messages at a user-selected display cadence (decoupled from render rate to survive high-volume chat); `components/` holds the dashboard panels (`LiveFeed`, `SummaryPanel`, `TranscriptionPanel`, charts, etc.); `types.ts` mirrors backend Pydantic models.

## Docs worth reading before larger changes

- `docs/architecture-notes.md` — design rationale: why Kafka is required on the main path, why ClickHouse, data/session modeling rules, deployment direction.
- `docs/project-flow.md` — Mermaid diagrams of the end-to-end flow and ClickHouse data shape.
- `docs/llm-summary-flow.md` — exact request/response/storage contract for the summary feature.
- `docs/change-log.md` — record notable behavior changes here when you make them (per `AGENTS.md`).
