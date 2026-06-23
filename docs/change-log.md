# Change Log

This log records local project changes, why they were made, and how they were verified.

## 2026-04-28 - Persist Local ClickHouse Data

### Reason

The Docker Compose ClickHouse service only mounted the initialization SQL file. ClickHouse stores table data under `/var/lib/clickhouse`, so recreating the container could remove local chat history.

### Change

Added a named Docker volume, `clickhouse-data`, mounted at `/var/lib/clickhouse` in `docker-compose.yml`.

### Result

Local ClickHouse data now survives `docker compose down`, container recreation, and service rebuilds. It is still deleted if volumes are explicitly removed, such as with `docker compose down -v`.

### Verification

```bash
docker compose config --quiet
```

Result: passed.

## 2026-04-28 - Increase Recent Message Window

### Reason

The frontend capped recent messages at 150 even though the backend API allows up to 1000 and the in-memory realtime hub defaults to 500.

### Change

Added `RECENT_MESSAGE_LIMIT = 500` in `frontend/src/config.ts`.

Updated the dashboard API request, live message merge helper, and live append path to use the shared limit.

### Result

The UI now keeps and displays up to 500 recent messages. Messages are stored oldest-to-newest internally, and the live feed renders them newest-first.

### Verification

```bash
cd frontend
npm run build
```

Result: passed.

## 2026-04-28 - Batch Live Message UI Updates

### Reason

The frontend previously updated multiple React states for every WebSocket chat message. During high-volume chat spikes, that could cause excessive rendering work and contribute to Chrome tab crashes such as `Aw, Snap! Error code 5`.

### Change

Changed the live message path in `frontend/src/App.tsx` to queue WebSocket messages immediately, then flush the queue into React state once per second.

The batched flush updates:

- per-channel recent message buffers
- visible live feed messages
- volume data
- top chatters
- top emotes

### Result

Incoming chat messages still arrive in order, but visible dashboard updates happen in one-second batches instead of once per message. Oldest messages are still dropped first when the recent message cap is reached.

### Verification

```bash
cd frontend
npm run build
```

Result: passed.

## 2026-04-28 - Combined Channel View and Chart Window Control

### Reason

The dashboard previously auto-selected the first configured channel, which made it harder to inspect all configured Twitch channels together. The volume chart also used a fixed 120-minute window.

### Change

Changed the channel selector so `All channels` is the default view. The frontend now sends no `channel` query parameter for that view, which lets the existing backend endpoints aggregate across every channel.

Added a `Chart window` selector with 15m, 1h, 2h, 6h, and 24h options. The selected window is sent to the volume endpoint as the `limit` value, and the live volume merge helpers use the same selected window.

### Result

The dashboard can now show combined recent messages, top chatters, top emotes, and message volume for all channels. Users can still switch to one channel from the dropdown, and the chart timescale can be changed independently.

### Verification

```bash
cd frontend
npm run build
```

Result: passed.

## 2026-04-28 - Optional Live Feed and Per-Channel Volume Charts

### Reason

The live feed can be expensive to render during high-volume chat, and the combined all-channel dashboard needed a quick way to compare channel volume without switching the main channel selector.

### Change

Added a `Live feed` checkbox to the dashboard controls. When unchecked, the live feed section is hidden while the rest of the dashboard continues to update.

Added per-channel volume charts above the live feed section. These charts appear only when `All channels` is selected. They use the existing volume analytics endpoint once per configured channel, with the same chart window selected for the main volume chart.

### Result

Users can reduce browser rendering work by hiding the live feed. In the combined view, each configured channel now has its own compact volume chart for quick comparison.

### Verification

```bash
cd frontend
npm run build
```

Result: passed.

## 2026-04-28 - Reduce Browser Work During Live Spikes

### Reason

Chrome still showed `Aw, Snap! Error code 5` during high-volume use. The frontend had already batched WebSocket updates, but each batch could still do expensive repeated sorting and render hundreds of live-feed rows.

### Change

Added a live flush safety cap so the browser processes at most the latest 1000 queued live messages per UI flush. ClickHouse/Kafka ingestion is unaffected because this only limits dashboard rendering work.

Changed top chatter and top emote live updates to aggregate counts once per batch instead of sorting once per message.

Changed the live feed to render only the latest 100 rows while still keeping the recent message buffer at 500.

Memoized derived metrics and chart data so unchanged message/chart arrays are not remapped on unrelated renders.

### Result

The dashboard does less work per second during busy chat bursts, reducing the chance that the browser renderer crashes. Historical data still comes from ClickHouse on refresh, so backend persistence remains complete.

### Verification

```bash
cd frontend
npm run build
```

Result: passed.

## 2026-04-29 - Configurable Dashboard Update Cadence

### Reason

The dashboard needed a user-selectable data display cadence so high-volume channels can update less frequently while lower-volume use can stay closer to live.

### Change

Added a `Display data` selector with these options:

- Live
- Every 10s
- Every 30s
- Every 1m
- Every 5m

The default is `Every 10s`. The selected value controls both queued WebSocket flushes into React state and the periodic dashboard reload from the API.

### Result

Users can tune browser workload without stopping ingestion. Slower display cadences reduce render pressure while Kafka and ClickHouse continue processing messages independently.

### Verification

```bash
cd frontend
npm run build
```

Result: passed.

## 2026-04-29 - Dashboard Request Spam Prevention

### Reason

The refresh button and request-producing selectors could be clicked or changed repeatedly, creating overlapping dashboard API requests.

### Change

Added an in-flight dashboard request guard in `frontend/src/App.tsx`. If a dashboard request is already running, additional refresh/interval calls return immediately instead of starting another request.

Added loading state to the controls in `frontend/src/components/ChannelControls.tsx`. While a dashboard request is running:

- channel selector is disabled
- chart window selector is disabled
- display cadence selector is disabled
- refresh button is disabled and shows `Updating`

The live feed visibility checkbox remains available because it only changes local rendering and does not make API requests.

### Result

Users get visible feedback during dashboard updates, and repeated clicks or selector changes cannot stack duplicate API requests.

### Verification

```bash
cd frontend
npm run build
```

Result: passed.

## 2026-04-29 - Add Kafka Data Explorer

### Reason

Local development needed a browser-based way to inspect Kafka topics, messages, offsets, partitions, and consumer groups.

### Change

Added Redpanda Console as `kafka-console` in `docker-compose.yml`, exposed on `http://localhost:8080`, and configured it to connect to the local Kafka broker at `kafka:29092`.

Updated `scripts/start.py` so `infra` and `all` start Kafka Console with Kafka and ClickHouse, then wait for the console port to become reachable.

Updated `README.md` with the Kafka Console URL and the primary chat topic name.

### Result

Developers can inspect the `twitch.chat.messages` topic and Kafka consumer groups from a local web UI.

### Verification

```bash
docker compose config --quiet
```

Result: passed.

## 2026-04-29 - Refresh Architecture and Flow Documentation

### Reason

Architecture documentation and project flow diagrams needed to reflect recent infrastructure and dashboard behavior changes.

### Change

Updated `docs/architecture-notes.md` to mention Redpanda Console, local ClickHouse volume persistence, the current frontend display cadence model, all-channel filtering, live feed hiding, and capped live feed rendering.

Updated `docs/project-flow.md` so the diagrams show Redpanda Console, Kafka offset commits after ClickHouse inserts, live message queuing, selected display cadence, and periodic API refreshes. Also clarified that only `chat_messages` is currently written while the other ClickHouse entities are planned.

### Result

The architecture and flow docs now match the current local stack and frontend data flow more closely.

### Verification

```bash
rg -n "chart deltas|aggregate delta|interval analytics|Redpanda|clickhouse-data|display cadence" docs/architecture-notes.md docs/project-flow.md
```

Result: stale realtime phrases removed; current infrastructure and display behavior are present.

## 2026-04-29 - Add Minimal-Cost AWS Compose Deployment Path

### Reason

The first cloud deployment target should preserve the current technologies while minimizing AWS cost. EKS and managed data services remain useful later, but they add fixed or higher baseline cost for a learning/staging environment.

### Change

Added `docker-compose.prod.yml` as a production override for a single EC2 host. It adds Caddy as the public reverse proxy, removes development bind mounts and reload commands from app containers, binds admin tools to localhost, and adds persistent Kafka/Caddy volumes.

Updated the frontend Dockerfile with a production target that builds the React app and serves static assets with Nginx.

Added `deploy/caddy/Caddyfile` for routing `/api`, `/health`, and `/ws` to the backend while serving frontend traffic through the production frontend container.

Added `docs/aws-deployment.md` with EC2 sizing, security group rules, `.env` values, deployment commands, SSH tunnels, backup notes, and scale-up paths. Updated architecture and flow docs to include the AWS Compose/Caddy deployment direction.

### Result

The repo now has a concrete low-cost AWS deployment path using one EC2 instance, Docker Compose, Caddy, Kafka, ClickHouse, and the existing app containers.

### Verification

```bash
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet
cd frontend && npm run build
cd backend && PYTHONPATH=. pytest
```

Result: passed.

## 2026-04-30 - Clarify Missing npm Frontend Startup Failure

### Reason

Starting the frontend through `scripts/start.py frontend` could fail unclearly when `npm` was not available on `PATH`, especially from IDE shells or environments without Node.js installed.

### Change

Updated `scripts/start.py` to check for `npm` before installing frontend dependencies or starting Vite. If missing, the launcher now prints an actionable message with Node.js and Docker Compose alternatives.

Updated `README.md` with the script-based frontend prerequisite and basic `npm install` / `npm run dev` commands.

### Result

Frontend startup now fails with a clear explanation when Node.js/npm is missing, while Docker Compose remains available as the no-local-Node path.

### Verification

```bash
which npm
which node
```

Result: both found in the current shell.

## 2026-04-30 - Fix Local Frontend Docker Target

### Reason

After adding the production frontend Docker stage, the local Compose frontend service could build the final Nginx image while still running the development command `npm run dev`. That image does not contain `npm`, causing:

```text
docker-entrypoint.sh: exec: line 47: npm: not found
```

### Change

Updated `docker-compose.yml` so the local frontend service explicitly builds the `dev` target from `frontend/Dockerfile`.

### Result

Local Docker Compose keeps using the Node/Vite development image, while `docker-compose.prod.yml` continues to override the frontend build target to `production`.

### Verification

```bash
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet
```

Result: passed.

## 2026-06-23 - Replace Live Feed WebSocket With SSE

### Reason

The live chat feed only ever pushed data from server to browser; the WebSocket handler never read anything meaningful from the client (it called `receive_text()` purely to detect disconnects). A one-directional channel doesn't need a bidirectional protocol, and WebSockets need explicit `Upgrade` handling in proxies/load balancers that plain HTTP doesn't.

### Change

Replaced `@router.websocket("/ws/messages")` with a Server-Sent Events endpoint at `GET /api/messages/stream` (`backend/app/api/routes.py`), backed by a `StreamingResponse` that emits `data: ...` frames and a `: keep-alive` comment every 15 seconds. `RealtimeHub` (`backend/app/storage/realtime.py`) now fans out to per-subscriber `asyncio.Queue` objects instead of holding raw `WebSocket` connections, and drops a subscriber if its queue fills up rather than letting one slow client stall broadcasts to everyone else. The frontend (`frontend/src/hooks/useLiveMessages.ts`) now uses `EventSource` instead of `WebSocket`. Renamed the `twitch_websocket_clients` Prometheus gauge to `twitch_sse_clients` and updated the Grafana panel. Removed the now-unused `/ws` proxy entries from `frontend/vite.config.ts` and `deploy/caddy/Caddyfile`.

### Result

The live feed now travels over plain HTTP, reconnects automatically via the browser's native `EventSource` retry behavior, and proxies through Caddy/Vite without any WebSocket-specific configuration. No behavior change for the dashboard itself.

### Verification

```bash
cd backend && PYTHONPATH=. pytest
cd frontend && npm run build
```

Result: passed. Also manually verified with `curl -D - http://127.0.0.1:8000/api/messages/stream` that the response is `200` with `content-type: text/event-stream` and a chunked body.
