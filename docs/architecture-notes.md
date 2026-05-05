# Twitch Chat Analytics Architecture Notes

## Project Goal

Build a Twitch chat ingestion and analytics platform to practice handling larger-scale streamed datasets. The project should target high-viewer Twitch channels and be designed around bursty chat volume, replayable event streams, and analytical queries over large message history.

## Application Shape

- Backend: FastAPI.
- Frontend: React.
- Twitch ingestion: Twitch IRC WebSocket by default, with EventSub retained as an optional mode.
- Realtime frontend updates: optional user-facing live dashboard, backed by backend WebSocket events.
- Database direction: ClickHouse for analytical event storage.
- Stream buffer: Kafka is required for v1 because the project is intended for scale practice.
- Kafka inspection: Redpanda Console is included in the local Docker Compose stack.
- Relational database: deferred until a later feature area, such as app users, saved dashboards, OAuth management, permissions, or billing.

## Core Pipeline

```text
Twitch IRC or EventSub
  -> FastAPI ingest service
  -> Kafka
  -> ClickHouse consumer
  -> ClickHouse analytics tables
  -> FastAPI query API
  -> React dashboard
```

Kafka is used as a durable buffer and replay layer between ingestion and database writes. It decouples Twitch message ingestion from ClickHouse persistence, absorbs traffic bursts, supports replay after schema/parser changes, and allows additional downstream consumers later.

ClickHouse is used as the primary analytical database because Twitch chat messages are append-heavy, event-based, time-windowed, and rarely updated after ingestion.

## Deployment Direction

Docker Compose remains the local development baseline. The lowest-cost AWS learning/staging deployment runs the same stack on a single EC2 instance with a production Compose override and Caddy as the public reverse proxy. Only ports `80`, `443`, and SSH from a trusted IP should be public; Grafana, Prometheus, Kafka Console, Kafka, and ClickHouse stay private to the host or Docker network.

EKS remains the Kubernetes learning and scale-up path. For a production SLO, Kafka should move to Amazon MSK or another managed Kafka service, and ClickHouse should move to ClickHouse Cloud or an actively operated ClickHouse cluster.

## Data Organization

Messages should be stored under both channel and session.

Core identifiers and timestamps:

- `channel_id`
- `channel_login`
- `channel_display_name`
- `session_id`
- `session_date`
- `stream_started_at`, when available
- `message_id`
- `event_ts`
- `received_at`

If exact Twitch stream session boundaries are unavailable, the system should fall back to a channel/day session. This keeps every message queryable by both channel and a practical session bucket.

## Message Data

Each message should preserve normalized fields and the raw Twitch IRC/EventSub payload.

Normalized fields should include:

- chatter user ID
- chatter login
- chatter display name
- message text
- message fragments
- badges
- emotes
- mentions
- reply metadata
- channel/session fields
- event timestamp
- backend received timestamp

The raw IRC line/tags or EventSub payload should be retained so future analytics can use metadata that was not modeled in the first version.

## Kafka Notes

Initial topic:

```text
twitch.chat.messages
```

Recommended Kafka message shape:

- key: `channel_id:session_id`
- value: normalized message JSON including raw Twitch IRC/EventSub payload

Kafka should be treated as required infrastructure for local development and production-like runs. The smallest implementation can still keep the number of services limited, but the architecture should not bypass Kafka for the main ingestion path.

Kafka is useful here because it provides:

- burst absorption
- replay
- durable handoff between ingestion and persistence
- decoupling between Twitch ingestion and ClickHouse writes
- a clean path for later consumers such as summaries, moderation signals, search indexing, and machine-learning features

Local Docker Compose includes Redpanda Console at `http://localhost:8080` for inspecting topics, messages, offsets, partitions, and consumer groups. It connects to the local broker through `kafka:29092`.

## ClickHouse Notes

ClickHouse is a column-oriented analytical database. It is a strong fit for queries such as:

- message counts per minute, hour, or day
- top users by message count
- top emotes by session
- unique chatters over time
- peak chat intervals
- aggregate analysis across large message history

It is less ideal for transactional application data, frequent row updates, strict relational constraints, and heavily normalized workflows. A relational database can be added later for app metadata without replacing ClickHouse as the analytics store.

The backend serializes queries through the shared ClickHouse client because `clickhouse-connect` sessions do not support concurrent queries. Dashboard endpoints should fail soft: recent messages can fall back to the live in-memory buffer, and analytics endpoints can return empty lists while logging ClickHouse errors.

In Docker Compose, ClickHouse data is stored in the named volume `clickhouse-data` mounted at `/var/lib/clickhouse`. This keeps local analytical data across container recreation unless Compose volumes are explicitly removed.

Likely tables:

- `channels`
- `stream_sessions`
- `chat_messages`
- `chat_interval_stats`
- `chat_summaries`

The `chat_messages` table should be append-only and optimized around channel/session/time queries. A likely ordering key is:

```sql
(channel_id, session_id, event_ts, message_id)
```

Partitioning should be date-based, with the exact granularity chosen once expected volume is clearer.

## Realtime Frontend

The frontend should be able to update in realtime as messages arrive, but ingestion must continue even when no browser is connected.

Backend WebSocket events should stream:

- individual normalized chat messages for the live feed
- channel/session status events

The frontend queues live WebSocket messages and flushes them into React state at a user-selected display cadence. Historical API queries supply page reloads, late joins, filters, chart windows, and periodic backfills. Dashboard controls can show all channels together or filter to one channel. The browser can hide the live feed and renders only a capped set of latest feed rows to reduce renderer pressure during high-volume chat.

## Monitoring

Prometheus and Grafana are part of the local scale-practice stack.

Prometheus should scrape:

- FastAPI backend `/metrics`
- ClickHouse consumer `/metrics`
- Kafka exporter metrics

Redpanda Console is available separately for interactive Kafka inspection, not Prometheus scraping.

Grafana should show:

- messages ingested per second
- messages by channel
- ingestion connected state
- Kafka publish rate
- Kafka consumer lag
- ClickHouse rows inserted per second
- ClickHouse insert latency and errors
- active frontend WebSocket clients

## Analytics Ideas

Realtime dashboard:

- live chat feed
- messages per minute
- active chatters
- unique chatters
- top chatters
- top emotes and emoji
- top words and phrases
- current session totals

Historical analytics:

- filter by channel, session, day, and time range
- messages per hour/day graph
- peak chat windows
- most active users per session/day
- most used emotes per session/day
- repeated/copypasta messages
- compare sessions for the same channel
- chatter concentration, such as whether a few users dominate chat

Optional LLM summaries:

- summarize 5-minute or 15-minute windows
- identify top topics
- identify common questions
- summarize chat mood
- explain notable spikes when enough context is available
- create stream recap summaries from chat windows

LLM calls should not receive every raw message indefinitely. Summaries should use pre-aggregated interval context, representative sampled messages, top terms, top emotes, common questions, and the previous interval summary.

## Deferred Ideas

Relational storage with sharding is intentionally deferred. It may be useful later for a different feature category, but it is not the best primary store for high-volume chat analytics.

Potential future relational use cases:

- app users
- OAuth credentials
- saved dashboards
- user preferences
- permissions
- annotations
- billing

Potential future processing consumers:

- moderation classification
- search indexing
- embeddings
- trend detection
- alerting
- LLM summary generation
