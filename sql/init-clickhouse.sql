CREATE DATABASE IF NOT EXISTS twitch_analyze;

CREATE TABLE IF NOT EXISTS twitch_analyze.channels
(
    channel_id String,
    channel_login LowCardinality(String),
    channel_display_name String,
    tracked_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(tracked_at)
ORDER BY channel_id;

CREATE TABLE IF NOT EXISTS twitch_analyze.stream_sessions
(
    session_id String,
    channel_id String,
    channel_login LowCardinality(String),
    session_date Date,
    stream_started_at Nullable(DateTime64(3, 'UTC')),
    stream_ended_at Nullable(DateTime64(3, 'UTC')),
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(session_date)
ORDER BY (channel_id, session_id);

CREATE TABLE IF NOT EXISTS twitch_analyze.chat_messages
(
    message_id String,
    eventsub_message_id String,
    channel_id String,
    channel_login LowCardinality(String),
    channel_display_name String,
    session_id String,
    session_date Date,
    chatter_user_id String,
    chatter_login LowCardinality(String),
    chatter_display_name String,
    message_text String,
    message_fragments String,
    badges String,
    emotes String,
    mentions String,
    reply String,
    raw_event String,
    event_ts DateTime64(3, 'UTC'),
    received_at DateTime64(3, 'UTC'),
    inserted_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(event_ts)
ORDER BY (channel_id, session_id, event_ts, message_id);

CREATE TABLE IF NOT EXISTS twitch_analyze.chat_interval_stats
(
    channel_id String,
    channel_login LowCardinality(String),
    session_id String,
    window_start DateTime64(3, 'UTC'),
    window_size LowCardinality(String),
    message_count UInt64,
    unique_chatter_count UInt64,
    top_chatters String,
    top_emotes String,
    top_terms String,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(window_start)
ORDER BY (channel_id, session_id, window_size, window_start);

CREATE TABLE IF NOT EXISTS twitch_analyze.chat_summaries
(
    summary_id String,
    channel_id String,
    channel_login LowCardinality(String),
    session_id String,
    window_start DateTime64(3, 'UTC'),
    window_size LowCardinality(String),
    summary_text String,
    model String,
    source_stats String,
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(window_start)
ORDER BY (channel_id, session_id, window_size, window_start, summary_id);

CREATE TABLE IF NOT EXISTS twitch_analyze.stream_transcript_segments
(
    segment_id String,
    channel_login LowCardinality(String),
    session_id String,
    segment_started_at DateTime64(3, 'UTC'),
    segment_ended_at DateTime64(3, 'UTC'),
    audio_path String,
    transcript_text String,
    model LowCardinality(String),
    status LowCardinality(String),
    error String,
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(segment_started_at)
ORDER BY (channel_login, session_id, segment_started_at, segment_id);
