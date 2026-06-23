from prometheus_client import Counter, Gauge, Histogram

CHAT_MESSAGES_INGESTED = Counter(
    "twitch_chat_messages_ingested_total",
    "Total normalized Twitch chat messages ingested.",
    ["source", "channel"],
)

KAFKA_MESSAGES_PUBLISHED = Counter(
    "twitch_kafka_messages_published_total",
    "Total chat messages successfully published to Kafka.",
    ["topic"],
)

KAFKA_PUBLISH_ERRORS = Counter(
    "twitch_kafka_publish_errors_total",
    "Total Kafka publish failures.",
    ["topic"],
)

KAFKA_PUBLISH_LATENCY = Histogram(
    "twitch_kafka_publish_latency_seconds",
    "Kafka publish latency in seconds.",
    ["topic"],
)

CLICKHOUSE_ROWS_INSERTED = Counter(
    "twitch_clickhouse_rows_inserted_total",
    "Total chat rows inserted into ClickHouse.",
)

CLICKHOUSE_INSERT_ERRORS = Counter(
    "twitch_clickhouse_insert_errors_total",
    "Total ClickHouse insert failures.",
)

CLICKHOUSE_INSERT_LATENCY = Histogram(
    "twitch_clickhouse_insert_latency_seconds",
    "ClickHouse batch insert latency in seconds.",
)

CLICKHOUSE_BATCH_SIZE = Histogram(
    "twitch_clickhouse_insert_batch_size",
    "ClickHouse insert batch sizes.",
    buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
)

SSE_CLIENTS = Gauge(
    "twitch_sse_clients",
    "Current connected frontend SSE clients.",
)

INGESTION_CONNECTED = Gauge(
    "twitch_ingestion_connected",
    "Whether Twitch ingestion is connected.",
    ["mode"],
)

WORKER_KAFKA_CONNECTED = Gauge(
    "twitch_worker_kafka_connected",
    "Whether the ClickHouse worker is connected to Kafka.",
)

WORKER_BATCHES_INSERTED = Counter(
    "twitch_worker_batches_inserted_total",
    "Total worker batches inserted into ClickHouse.",
)
