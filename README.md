# Twitch Analyze

Twitch Analyze is a FastAPI + React application for ingesting Twitch chat at scale-practice volumes. Chat messages flow through Kafka, land in ClickHouse, and are exposed through REST and WebSocket APIs for realtime and historical analytics.

## Architecture

```text
Twitch IRC or EventSub
  -> FastAPI ingest service
  -> Kafka topic: twitch.chat.messages
  -> ClickHouse consumer
  -> ClickHouse analytics tables
  -> FastAPI query API
  -> React dashboard
```

See [docs/architecture-notes.md](docs/architecture-notes.md), [docs/project-flow.md](docs/project-flow.md), [docs/kubernetes-roadmap.md](docs/kubernetes-roadmap.md), and [docs/troubleshooting-log.md](docs/troubleshooting-log.md) for design notes, diagrams, the Kubernetes learning path, and the running troubleshooting log.

For a minimal-cost AWS learning/staging deployment, see [docs/aws-deployment.md](docs/aws-deployment.md). That path uses one EC2 instance with Docker Compose, Caddy, Kafka, ClickHouse, and the existing app containers.

For a near-free GCP experiment managed with Terraform, see [docs/gcp-terraform-deployment.md](docs/gcp-terraform-deployment.md). That path targets one Compute Engine `e2-micro` VM and disables optional monitoring services.

## Local Setup

1. Copy environment defaults:

   ```bash
   cp .env.example .env
   ```

2. Set `TWITCH_CHANNELS` in `.env`. Credentials are optional for the default IRC read mode.

3. Start Kafka, ClickHouse, backend, worker, and frontend:

   ```bash
   docker compose up --build
   ```

   For local script-based frontend runs, install Node.js/npm first. The frontend uses Vite and expects `npm` on `PATH`:

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

4. Open:

   - Frontend: http://localhost:5173
   - Backend API: http://localhost:8000/docs
   - ClickHouse HTTP: http://localhost:8123
   - Kafka Console: http://localhost:8080
   - Prometheus: http://localhost:9090
   - Grafana: http://localhost:3000

Kafka Console is a Redpanda Console instance pointed at the local Kafka broker. Use it to inspect topics, message payloads, offsets, partitions, and consumer groups. The chat topic is:

```text
twitch.chat.messages
```

## Kubernetes Practice

Docker Compose remains the fastest local development path. A Docker Desktop Kubernetes baseline is available under [k8s/local](k8s/local) for production-behavior practice:

```bash
docker build -t twitch-analyze-backend:local ./backend
docker build -t twitch-analyze-frontend:local ./frontend
kubectl apply -f k8s/local/namespace.yaml
kubectl apply -f k8s/local/
```

## Twitch Ingestion

The default ingestion mode is Twitch IRC:

```bash
TWITCH_INGESTION_MODE=irc
TWITCH_CHANNELS=some_channel,another_channel
```

IRC mode can read public chat through the Twitch IRC WebSocket and publish normalized messages into Kafka. If `TWITCH_USERNAME` and `TWITCH_ACCESS_TOKEN` are omitted, the app uses an anonymous `justinfan`-style IRC connection for read-only ingestion.

Authenticated IRC mode is also supported:

```bash
TWITCH_USERNAME=your_twitch_login
TWITCH_ACCESS_TOKEN=oauth_token_with_chat_read
```

For the original EventSub path, set:

```bash
TWITCH_INGESTION_MODE=eventsub
```

The EventSub backend expects a Twitch user access token that can subscribe to chat message events. At minimum, the token should include `user:read:chat`. The authenticated user ID is used as the `user_id` condition for `channel.chat.message` subscriptions.

Required values:

- `TWITCH_CHANNELS`

Required for EventSub mode:

- `TWITCH_CLIENT_ID`
- `TWITCH_CLIENT_SECRET`
- `TWITCH_ACCESS_TOKEN`

Optional:

- `TWITCH_USERNAME`
- `TWITCH_USER_ID`
- `TWITCH_REFRESH_TOKEN`

If `TWITCH_USER_ID` is omitted, the backend validates the access token with Twitch and uses the returned user ID.

## Development Without Twitch

Infrastructure and dashboard can run without Twitch credentials. Live ingestion starts when `TWITCH_CHANNELS` is set. If `TWITCH_CHANNELS` is blank, ingestion stays disabled and historical endpoints still query ClickHouse.

## Monitoring

The local Docker stack includes Prometheus and Grafana.

Prometheus scrapes:

- backend FastAPI metrics at `backend:8000/metrics`
- ClickHouse worker metrics at `worker:9101/metrics`
- Kafka exporter metrics at `kafka-exporter:9308/metrics`

Grafana is available at:

```text
http://localhost:3000
```

Default local credentials:

```text
admin / admin
```

The starter dashboard is provisioned as `Twitch Analyze Overview`. It includes:

- messages ingested per second
- ingestion connected state
- frontend WebSocket clients
- Kafka publish rate
- Kafka consumer lag
- ClickHouse rows inserted per second

The backend also exposes Prometheus metrics at:

```text
http://localhost:8000/metrics
```

The worker exposes metrics at:

```text
http://localhost:9101/metrics
```

The React analytics chart updates from historical ClickHouse queries and from live WebSocket messages. If ClickHouse has not received data yet, live messages still update the chart immediately while the worker catches up.

Dashboard API endpoints are resilient to temporary ClickHouse query errors: recent messages fall back to the in-memory live buffer, and analytics endpoints return empty lists while logging the backend exception. The ClickHouse repository serializes access through its shared client because `clickhouse-connect` does not allow concurrent queries in the same session.

## Security Review

Security status from the latest repo pass:

- ✅ No real Twitch, OpenAI, GitHub, Terraform, or ClickHouse secrets were found in committed source files. Keep using `.env`, `terraform.tfvars`, and `k8s/local/secret.yaml` locally; those paths are ignored by git.
- ✅ Frontend dependency audit passes after updating Vite to `8.0.16`.
- ⚠️ The backend does not implement application-level authentication or authorization. Treat `/api/messages`, `/api/summaries/generate`, `/api/transcriptions/start`, `/metrics`, and `/ws/messages` as trusted-network endpoints unless an authenticated reverse proxy or API auth layer is added.
- ⚠️ `docker-compose.yml` is a local development stack and publishes Kafka, ClickHouse, backend, metrics, Grafana, Prometheus, and Kafka Console ports. Do not expose it directly to the internet.
- ⚠️ `docker-compose.prod.yml` removes public Kafka, ClickHouse, backend, and worker ports, but still exposes the frontend/API through Caddy. Add access control before using write, summary, or transcription endpoints in a shared or public deployment.
- ⚠️ Local defaults use `CLICKHOUSE_PASSWORD=twitch_analyze` and Grafana `admin / admin`. Rotate both for any non-local environment.
- ⚠️ Chat summaries and audio transcription send sampled chat/audio content to OpenAI when `OPENAI_API_KEY` and those features are enabled. Only enable them for channels and environments where that data flow is acceptable.
- ⚠️ The GCP Terraform startup script writes sensitive values into instance startup metadata and the VM `.env`. Prefer short-lived tokens, least-privilege service accounts, and Secret Manager for production-grade deployments.

## Backend Commands

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Run the ClickHouse consumer separately:

```bash
cd backend
. .venv/bin/activate
python -m app.workers.clickhouse_consumer
```

## Frontend Commands

```bash
cd frontend
npm install
npm run dev
```

## Startup Scripts

The `scripts/start.py` launcher is intended to work from PyCharm or a terminal. It reads root `.env`, uses the project `.venv` when present, and starts each service with the correct working directory.

Terminal usage:

```bash
scripts/start-infra.sh
scripts/start-backend.sh
scripts/start-worker.sh
scripts/start-frontend.sh
scripts/start-all.sh
```

Equivalent Python launcher commands:

```bash
.venv/bin/python scripts/start.py infra
.venv/bin/python scripts/start.py backend
.venv/bin/python scripts/start.py worker
.venv/bin/python scripts/start.py frontend
.venv/bin/python scripts/start.py all
```

PyCharm usage:

- Open `scripts/start.py`.
- Create a Python run configuration using the project `.venv` interpreter.
- Set the working directory to the repo root.
- Set script parameters to one of: `infra`, `backend`, `worker`, `frontend`, or `all`.

`all` starts Kafka and ClickHouse first, waits until they are reachable, then starts backend, worker, and frontend. If you run services separately, start `infra` before `backend` or `worker`. The frontend launcher will run `npm install` automatically if `frontend/node_modules` is missing; pass `--no-install` to disable that behavior.

For PyCharm, the simplest run configuration is:

```bash
scripts/start.py all
```

That avoids `ECONNREFUSED` errors from starting the frontend before the backend or starting the backend before ClickHouse.

Shared PyCharm run configurations are included under `.idea/runConfigurations`:

- `Start Infra`
- `Debug Backend (FastAPI)`
- `Debug Worker (ClickHouse Consumer)`
- `Run Frontend (Vite)`

Use this debugger workflow:

1. Run `Start Infra`.
2. Debug `Debug Backend (FastAPI)`.
3. Debug `Debug Worker (ClickHouse Consumer)` if you need Kafka/ClickHouse consumer breakpoints.
4. Run `Run Frontend (Vite)`.

The backend debug config intentionally does not use `--reload`, because reload mode starts a child process and can make PyCharm breakpoints unreliable.

## ClickHouse Authentication

The local stack uses the `default` ClickHouse user with password `twitch_analyze`. If you created `.env` before this default existed, either set:

```bash
CLICKHOUSE_PASSWORD=twitch_analyze
```

or leave it blank and let the startup code/Compose default apply.

If Docker already created a ClickHouse container with different credentials and you see `AUTHENTICATION_FAILED`, recreate the ClickHouse container:

```bash
docker compose down
docker compose up --build --force-recreate
```

## ClickHouse Local Persistence

The Docker Compose ClickHouse service stores local data in the named volume `clickhouse-data`, mounted at `/var/lib/clickhouse`.

This means local ClickHouse data survives normal container stops, `docker compose down`, and service recreation. It is deleted if you explicitly remove Compose volumes:

```bash
docker compose down -v
```
