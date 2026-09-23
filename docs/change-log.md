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

## 2026-07-19 - Dashboard UI/UX Overhaul

### Reason

The dashboard stacked every panel in one long column, burying the live feed below rarely-used panels. The main chart plotted two unlabeled series on a category axis that silently compressed multi-hour data gaps, several control labels were ambiguous ("Display data", "Chart window"), metric cards lacked units/scope, Markdown summaries rendered as raw text in a `<pre>`, and the live feed had wrapping timestamps, no bot filtering, and shifted rows under the reader on every flush.

### Change

Frontend-only, in `frontend/src`:

- **Layout**: two-column desktop layout — analytics on the left, Live Feed as a sticky right column (`.dashboard-layout` in `styles.css`, stacks to one column under 1200px). Chat Summary and Streamer Audio panels are collapsible and default collapsed (auto-expand on activity). Compact sticky topbar.
- **Messages Per Minute chart** (`VolumeChart.tsx`): legend and named series, minute-granularity axis labels, quiet minutes zero-filled and data clipped to the selected window so the time axis no longer hides gaps.
- **Live Feed** (`LiveFeed.tsx`): 24-hour timestamps, channel column hidden when a single channel is selected, "Hide bots" toggle (shared `KNOWN_BOTS` list in `config.ts`, also filters Top Chatters), and scroll-to-pause with an "N new" resume pill so rows stop shifting while reading history.
- **Labels**: "Chart window" → "Time window", "Display data" → "Update every", "Live feed" → "Live updates" (it gates the SSE connection, not just the feed); metric cards gained scope/unit sublabels.
- **Summaries** (`SummaryPanel.tsx`): summary text rendered as Markdown via `react-markdown` (new dependency), skeleton shown while generating, generate controls hidden until a channel is selected.
- **Channel Volume** (`ChannelVolumeCharts.tsx`): cards sorted by activity, peak rate shown, no-data cards dimmed.
- **Misc**: favicon added (`index.html`), visible focus state and `aria-label` for the toggle switch, manual Refresh demoted to a ghost button next to an "Updated Ns ago" indicator, hover glow removed from inputs.

### Result

The live feed is visible alongside the analytics instead of below the fold, the volume chart is honest about gaps and legible without guessing what each line is, summaries render as formatted Markdown, and high-volume feeds can be read without rows moving underfoot.

### Verification

```bash
cd frontend && npm run build
```

Result: passed. Also manually verified against the live stack (all-channels and single-channel views, bot filter, feed pause/resume pill, collapsible panels, 1440px and 390px viewports) via a local Vite server on port 5174, since the Dockerized frontend's bind mount was in a broken state at the time.

## 2026-07-19 - Full-Codebase Audit Fixes

### Reason

A four-track audit (backend pipeline, API/services, frontend, infra/scripts) surfaced 40+ defects: silent ingestion death paths, unbounded memory growth, a chart-corrupting bucket-key mismatch, unauthenticated resource-spawning endpoints, analytics queries that assumed ReplacingMergeTree dedup at read time, and a `scripts/start.py` that was broken on Windows.

### Change

**Ingestion resilience** (`backend/app/ingestion`, `main.py`, `storage/kafka.py`): EventSub channel resolution moved inside the retry loop (a transient Helix failure no longer permanently kills ingestion); reconnect backoff now also applies to clean server closes (no more zero-delay busy loop on bad OAuth); EventSub `session_reconnect` now follows `reconnect_url` to avoid a message gap; a Kafka publish failure is logged instead of tearing down the IRC connection, and the RealtimeHub broadcast always runs; the chat hot path uses batched `producer.send()` (acks=all kept) instead of per-message `send_and_wait`; lifespan shutdown steps are independently protected so cleanup always completes; IRC fallback message IDs use a deterministic sha1; IRCv3 tag unescaping rewritten as a single left-to-right scan.

**Workers** (`clickhouse_consumer.py`, `transcript_consumer.py`, `audio_capture.py`): consumers stop fetching while a batch is awaiting an insert retry (bounded memory during ClickHouse outages); offset commits are wrapped so a rebalance can't crash the worker, and `consumer.stop()` is guaranteed; audio chunk processing moved inside the per-channel retry loop; streamlink/ffmpeg stderr is continuously drained into a bounded buffer to prevent pipe-buffer deadlock.

**API** (`routes.py`, `summaries.py`, `config.py`, `storage/clickhouse.py`, `realtime.py`): optional `API_AUTH_TOKEN` — when set, mutating endpoints require `X-API-Key`; transcription jobs get a concurrency cap (`TRANSCRIPTION_MAX_CONCURRENT_JOBS`, 429 at cap), bounded job history, and sanitized failure details; `POST /api/messages` capped at 500 messages per request; OpenAI calls get a 60s timeout (`OPENAI_TIMEOUT_SECONDS`); an explicitly empty ClickHouse password is no longer silently replaced with the committed default; analytics queries are duplicate-safe (`uniqExact(message_id)` / `LIMIT 1 BY message_id`) since the pipeline is at-least-once; `ClickHouseRepository.connect()` classmethod constructs off the event loop; SSE subscribers dropped for overflow now receive a close sentinel so clients reconnect instead of idling on a dead stream.

**Frontend** (`analytics.ts`, `App.tsx` in commit 369f6dc, `useLiveMessages.ts`, `client.ts`): volume bucket keys normalized through one helper, fixing the live/DB merge mismatch that split every minute into duplicate chart points; channel-switch race fixed with a request-id staleness guard; live volume recomputed from the deduped message cache instead of per-flush `Math.max` deltas; EventSource reconnects with backoff after fatal closes; all fetches carry a 15s timeout; the pending live-message queue is capped at enqueue time; SSE frames parse inside try/catch.

**Infra/scripts** (`docker-compose*.yml`, `sql/init-clickhouse.sql`, `scripts/start.py`, `backend/Dockerfile`, `k8s/local`): prod overlay now covers `transcript-worker` and `audio-capture` (restart policy, no published ports, no dev bind mounts); all dev published ports bind to 127.0.0.1; Grafana/ClickHouse passwords are env-driven and required in prod (`:?` guards); 180-day TTLs on `chat_messages` and `stream_transcript_segments` (fresh volumes only); Kafka topic defaults pinned (3 partitions, 14-day retention); dead `chat_interval_stats` table and `VITE_BACKEND_WS_PROXY_TARGET` removed everywhere including k8s manifests; `start.py` works on Windows (venv path, npm resolution, process-group cleanup so Vite no longer orphans port 5173); backend container runs as non-root.

### Result

Ingestion survives transient Twitch/Kafka/ClickHouse failures instead of dying silently; memory is bounded under outages and chat spikes; charts merge live and historical data correctly; the API can be token-protected and can no longer be used for unbounded subprocess/OpenAI spend; analytics don't overcount after replays; the local dev scripts work on Windows.

### Verification

```bash
cd backend && PYTHONPATH=. pytest        # 38 passed, 0 failed
cd frontend && npm run build             # tsc + vite clean
docker compose config --quiet            # passed
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet  # passed
python scripts/start.py --help           # parses; Windows paths resolve
```

Note: backend tests now run only on the asyncio anyio backend (`tests/conftest.py`) — trio (a transitive streamlink dependency) previously duplicated every async test against a backend the app doesn't use.

## 2026-09-23 - VOD Chat-Peak Analysis and UI Overhaul

### Reason

The dashboard only covered live chat. There was no way to look back at a past broadcast and find the moments chat reacted to. The UI also hard-coded one dark palette in a single 900-line `styles.css`, which had no shared primitives and no theme choice.

### Change

**VOD replay ingestion** (`backend/app/ingestion/vod_replay.py`): `TwitchGqlClient` reads VOD metadata and cursor-paged chat replay from Twitch's web GQL API, with retries for 429, 5xx and transient GQL errors, a guard against stalled cursors, and dedup at page boundaries. `normalize_comment` converts each comment to a `ChatMessage` with `source='vod'`, `session_id='vod:<video_id>'` and `event_ts` set to the original air time (`createdAt` plus the content offset). Replay chat is published to the normal `twitch.chat.messages` topic and inserted by the existing ClickHouse consumer. It does not bypass Kafka. The GQL URL, client id and query hash are settings. The default client id is TwitchDownloader's (`kd1unb4b3q4t58fwlpcbzcbnm76a8fp`) because the browser web id now fails Twitch's integrity check on the second page.

**Storage** (`sql/init-clickhouse.sql`, `k8s/local`, `storage/clickhouse.py`, `storage/kafka.py`, `workers/clickhouse_consumer.py`): new `chat_messages.source LowCardinality(String) DEFAULT 'live'` column. All live dashboard queries now filter `source='live'`, and VOD queries filter by `session_id` and `source='vod'`. The 180-day TTL moved from `event_ts` to `received_at`, so imported chat from old VODs doesn't expire on insert. New `vod_analyses` table (`ReplacingMergeTree(updated_at)`, ordered by `video_id`, peaks as JSON). `ensure_vod_schema()` is an idempotent runtime migration (add column, re-key TTL with `materialize_ttl_after_modify=0`, create table) that runs at startup in both the API and the consumer worker. The worker retries it with backoff. Both processes log `VOD schema ensured ...`. `KafkaJsonProducer.flush()` was added.

**Analysis** (`backend/app/services/vod_analysis.py`, `models/vod.py`): `VodAnalysisService` runs fetch, publish, `flush()`, consumer catch-up wait (stable `uniqExact` count at 98% or more of the fetched count, 15 min timeout), then bucketing (5 s up to `VOD_MAX_BUCKETS`), peak detection and `vod_analyses` upsert. The job reports progress on an in-memory `VodAnalysisJob`. Peak detection smooths the counts with a 3-point moving average, then computes a robust z-score against a rolling median and MAD over a ~10-minute window (sigma is at least the Poisson floor). It keeps local maxima with z >= 3 and at least 1.5x the baseline, enforces a 90 s minimum gap, caps results at `VOD_MAX_PEAKS`, and grows extents to 35% of the height above the baseline (at most 120 s per side). Each peak gets top emotes, top tokens, sample messages and a heuristic label (for example `LUL · xdd · 9.8 msg/s`). `VodLabelService` can add OpenAI titles (`OPENAI_VOD_LABEL_MODEL`, which falls back to the summary model).

**API** (`backend/app/api/routes.py`, `main.py`): `POST /api/vods/analyze` returns 202 with a job. It returns the stored completed analysis with no job unless `force` is set. It enforces a concurrency cap (429) and skips the fetch on a re-run after a catch-up timeout. Also added: `GET /api/vods`, `GET /api/vods/{id}`, `GET /api/vods/{id}/activity` (optional `bucket_seconds`, capped at 3600 buckets) and `POST /api/vods/{id}/label`. The lifespan calls `ensure_vod_schema()` once after connecting and cancels running VOD tasks on shutdown.

**Frontend VOD view** (`frontend/src/features/vod/**`, `api/vods.ts`, `twitch-embed.d.ts`): a URL/id form with force re-import, a live job status and progress bar (fetching, ingesting, analyzing), and the Twitch player embed. An SVG activity bar shows bars, highlighted peak extents, labelled peak markers with collision avoidance, a clamped hover tooltip and a playhead that follows playback. Clicking the bar or a marker seeks the player. The view also has a peak list (time, title or label, keywords, rate; the active peak is highlighted), a stats and bucket-size side card, "Label peaks with AI", and recent analyses. The open VOD is restored from sessionStorage when switching views or reloading. `VodView` is wrapped in `React.memo`.

**UI overhaul** (`frontend/src/styles/{tokens,base,components,layout}.css` replace `styles.css`; `components/ui/*`; `hooks/useTheme.ts`, `useThemeColors.ts`): light and dark design tokens and a theme toggle (follows the system setting until the user picks a theme, then localStorage; `data-theme` on `<html>`), plus shared primitives (Button, Card, Segmented, ChartTooltip, SeriesLegend, ThemeToggle). Charts read token colors. A Live/VODs segmented nav is persisted in `location.hash` (`#live` / `#vods`). The UI font is Inter via `@fontsource-variable/inter`.

**Integration fixes**: lazy `TODO(integration)` imports became top-level imports from `app.ingestion.vod_replay`. The VOD side-card stats had inherited the `.stat` primitive's padding and wrapped mid-number ("6,67 / 3"). The peak list showed the peak time but seeked to the start time and now shows the start time. At 390 px the peak list drops the rate column and narrows the time column so titles fit.

Behavior notes:

- The busy spinner only shows on buttons with `.is-busy`.
- Panel-local state (collapsed/expanded, drafts) resets when switching between Live and VODs, because the inactive view unmounts. The open VOD itself is restored from sessionStorage.
- `api/client.ts` now surfaces the backend `detail` text for failed GET requests (`HttpError`), not just the status code.
- The client does not send `X-API-Key`, same as the summary and transcription POSTs. `require_api_key` is a no-op unless `API_AUTH_TOKEN` is set. If it is set, the VOD analyze and label POSTs are rejected like the others.
- The Twitch embed's `parent` is the page hostname, so the player only loads on `localhost` or an HTTPS domain, not on `127.0.0.1` or a LAN IP.

### Result

A past broadcast can be analyzed from the VODs tab: chat replay flows through Kafka into ClickHouse, peaks are detected and labelled, and the embedded player jumps to each one. Live analytics are unaffected by imports. The whole dashboard supports light and dark themes on shared primitives.

### Verification

```bash
cd backend && PYTHONPATH=. pytest      # 164 passed
cd frontend && npm ci && npm run build # tsc + vite clean (existing >500 kB chunk warning only)
docker compose config --quiet          # passed
```

End to end with `docker compose up --build` on an existing ClickHouse volume (469,747 live rows before the imports):

- Migration: `DESCRIBE chat_messages` shows `source LowCardinality(String) DEFAULT 'live'`. `SHOW CREATE TABLE` shows `TTL toDateTime(received_at) + toIntervalDay(180)`. `vod_analyses` exists. The backend and worker both logged `VOD schema ensured`.
- The first import failed after page 1 with `failed integrity check` on the browser web client id. Switching the default client id fixed it (commit "Use chat-replay GQL client id that passes integrity check").
- Short VOD `2772087973` (pokimane, 30:14): 37 pages, 2,088 comments fetched and stored, completed in ~14 s. 2,044 messages inside the duration, 547 chatters, 5 s buckets, 3 peaks (`pokiHype · chocoChad · 4.4 msg/s` at 1:10, `pokiPOP · earthostepHehe · 4.4 msg/s` at 28:05, `<3 · pokiHype · 10 msg/s` at 29:55). `/activity`: 363 buckets, 357 non-zero, max 51. AI titles (`gpt-5.2`): "Birthday wishes and gifted subs", "Laughing at random lyrics", "Bye and happy birthday". Re-submitting without `force` returned the stored analysis with `job: null` (~0.1 s).
- Multi-hour VOD `2876131003` (ludwig, 3:23:14): 375 pages, 21,533 comments fetched and stored in ~110 s. 21,507 messages, 3,632 chatters, 30 s buckets, 4 peaks. `/activity`: 407 buckets, all non-zero.
- VOD `2840122882` (sodapoppin, 36:19), run through the UI: 6,765 comments, 6 peaks, AI-titled ("Look up at the sky", "Real voice reveal reactions", ...).
- Live isolation: pokimane (not ingested live) still shows `message-total` 0 and empty volume, top chatters and top emotes after the import. The overall `message-total` (469,816) equals the `source='live'` row count exactly. `SELECT source, count()` gave `live 469816`, `vod 30386` (3 sessions).
- UI (Playwright, compose frontend on `localhost:5173`): the Live view renders as before. The VODs tab went through the fetching and completed states. Clicking a peak marker seeked the embed to the peak start. The playhead advanced ~1 s/s during playback. "Label peaks with AI" filled the titles. The theme toggle restyles the VOD view. The hover tooltip stays inside the bar at both edges. There is no horizontal overflow at 390 px.

Screenshots: `docs/screenshots/ui-overhaul-{dark,light}-1440.png`, `ui-overhaul-{dark,light}-390.png`, `ui-overhaul-{dark,light}-1440-sample-data.png`, `vod-view-dark-1440.png`, `vod-view-light-1440.png`, `vod-view-dark-390.png`.
