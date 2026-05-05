# Repository Guidelines

## Project Structure & Module Organization

This repository is a Twitch chat analytics stack. Backend code lives in `backend/app`, with API routes under `backend/app/api`, ingestion clients under `backend/app/ingestion`, storage integrations under `backend/app/storage`, and background consumers under `backend/app/workers`. Backend tests live in `backend/tests`.

Frontend code lives in `frontend/src`, with reusable UI in `frontend/src/components`, API calls in `frontend/src/api`, hooks in `frontend/src/hooks`, and shared types/utilities in `frontend/src/types.ts` and `frontend/src/utils`.

Infrastructure and operations files are in `docker-compose.yml`, `monitoring/`, `k8s/local/`, `sql/`, and `scripts/`. Project notes and change history are in `docs/`.

## Build, Test, and Development Commands

- `docker compose up --build`: starts the full local stack.
- `.venv/bin/python scripts/start.py infra`: starts Kafka, ClickHouse, and Kafka Console.
- `.venv/bin/python scripts/start.py all`: starts infra, backend, worker, and frontend.
- `cd backend && PYTHONPATH=. pytest`: runs backend tests.
- `cd frontend && npm run dev`: starts Vite locally.
- `cd frontend && npm run build`: type-checks and builds the frontend.
- `docker compose config --quiet`: validates Compose syntax.

Kafka Console is available at `http://localhost:8080`; the main topic is `twitch.chat.messages`.

## Coding Style & Naming Conventions

Use Python 3 type hints and async patterns consistently. Keep backend modules snake_case and classes PascalCase. Use Pydantic models for structured data instead of ad hoc dictionaries where practical.

Frontend uses TypeScript React. Components should be PascalCase, hooks should start with `use`, and utility functions should be camelCase. Keep props typed explicitly. There is no repository-wide formatter configured, so match surrounding style and avoid unrelated formatting churn.

## Testing Guidelines

Backend tests use `pytest`; name files `test_*.py` and keep tests near the behavior they cover. Add regression tests for parser, storage, worker, and route changes. Frontend currently relies on TypeScript build verification; run `npm run build` for frontend changes.

## Commit & Pull Request Guidelines

Existing commits use short imperative summaries, for example `Add local Kubernetes manifests` and `Improve infra startup resilience`. Follow that style.

Pull requests should include a concise description, verification commands run, linked issue if applicable, screenshots for UI changes, and notes for config or infrastructure changes.

## Security & Configuration Tips

Copy `.env.example` to `.env` for local setup. Do not commit real Twitch credentials, access tokens, generated secrets, or local data volumes. Document notable behavior changes in `docs/change-log.md`.
