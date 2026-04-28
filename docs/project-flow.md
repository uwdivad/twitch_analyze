# Project Flow Diagram

## End-to-End Architecture

```mermaid
flowchart LR
    twitch[Twitch IRC WebSocket<br/>or EventSub WebSocket]
    ingest[FastAPI Ingest Service]
    kafka[(Kafka<br/>twitch.chat.messages)]
    consumer[ClickHouse Consumer<br/>Batch Writer]
    clickhouse[(ClickHouse)]
    api[FastAPI Query API]
    realtime[FastAPI Realtime WebSocket]
    react[React Dashboard]
    llm[Optional LLM Summary Worker]
    prometheus[Prometheus]
    grafana[Grafana]

    twitch -->|chat messages/events| ingest
    ingest -->|normalized message JSON<br/>key: channel_id:session_id| kafka
    ingest -->|live message/status events| realtime

    kafka -->|batched consume| consumer
    consumer -->|batched inserts| clickhouse

    kafka -.->|future consumer| llm
    llm -.->|interval summaries| clickhouse

    clickhouse -->|historical queries| api
    api -->|REST responses| react
    realtime -->|live feed + chart deltas| react

    api -.->|/metrics| prometheus
    consumer -.->|/metrics| prometheus
    kafka -.->|exporter metrics| prometheus
    prometheus --> grafana
```

## Ingestion And Persistence Flow

```mermaid
sequenceDiagram
    participant Twitch as Twitch IRC/EventSub
    participant Ingest as FastAPI Ingest Service
    participant Kafka as Kafka Topic
    participant Consumer as ClickHouse Consumer
    participant CH as ClickHouse

    Twitch->>Ingest: IRC PRIVMSG or channel.chat.message event
    Ingest->>Ingest: normalize message
    Ingest->>Ingest: attach channel/session metadata
    Ingest->>Kafka: publish message
    Kafka-->>Consumer: consume in batches
    Consumer->>Consumer: dedupe by message_id/event metadata
    Consumer->>CH: batch insert chat_messages
    Consumer->>CH: update/insert interval analytics
```

## Realtime Frontend Flow

```mermaid
sequenceDiagram
    participant Twitch as Twitch IRC/EventSub
    participant Ingest as FastAPI Ingest Service
    participant WS as FastAPI WebSocket
    participant UI as React Dashboard
    participant API as FastAPI Query API
    participant CH as ClickHouse

    UI->>WS: connect to live updates
    UI->>API: fetch current channel/session state
    API->>CH: query recent/historical data
    CH-->>API: analytics results
    API-->>UI: initial dashboard data

    Twitch->>Ingest: new chat message
    Ingest->>WS: broadcast live message
    Ingest->>WS: broadcast aggregate delta
    WS-->>UI: update chat feed
    WS-->>UI: update charts/counters
```

## ClickHouse Data Shape

```mermaid
erDiagram
    CHANNELS ||--o{ STREAM_SESSIONS : has
    STREAM_SESSIONS ||--o{ CHAT_MESSAGES : contains
    STREAM_SESSIONS ||--o{ CHAT_INTERVAL_STATS : aggregates
    STREAM_SESSIONS ||--o{ CHAT_SUMMARIES : summarizes

    CHANNELS {
        string channel_id
        string channel_login
        string channel_display_name
        datetime tracked_at
    }

    STREAM_SESSIONS {
        string session_id
        string channel_id
        date session_date
        datetime stream_started_at
        datetime stream_ended_at
    }

    CHAT_MESSAGES {
        string message_id
        string channel_id
        string session_id
        string chatter_user_id
        string chatter_login
        string message_text
        json message_fragments
        json badges
        json raw_event
        datetime event_ts
        datetime received_at
    }

    CHAT_INTERVAL_STATS {
        string channel_id
        string session_id
        datetime window_start
        string window_size
        int message_count
        int unique_chatter_count
        json top_chatters
        json top_emotes
        json top_terms
    }

    CHAT_SUMMARIES {
        string summary_id
        string channel_id
        string session_id
        datetime window_start
        string window_size
        string summary_text
        string model
        datetime created_at
    }
```

## Flow Notes

- Kafka is required in the main ingestion path so the project exercises durable streamed-data handling from the beginning.
- ClickHouse is the analytical store for append-only chat events, rollups, and summaries.
- Realtime browser connections are optional. Ingestion and persistence continue even if no React dashboard is open.
- Relational storage is deferred and can later own transactional app metadata without replacing Kafka or ClickHouse.
- Prometheus scrapes backend, worker, and Kafka exporter metrics. Grafana provides the operational dashboard.
