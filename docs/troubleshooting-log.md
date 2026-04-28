# Troubleshooting Log

This log records notable errors seen during local development, the diagnosis, and the change made in response. It is intentionally practical rather than exhaustive.

## 2026-04-28 - Kafka Startup Race

### Symptoms

The ClickHouse worker repeatedly logged Kafka connection failures at startup:

```text
INFO:aiokafka.consumer.subscription_state:Updating subscribed topics to: frozenset({'twitch.chat.messages'})
ERROR:aiokafka:Unable connect to "kafka:29092": [Errno 111] Connect call failed ('172.18.0.4', 29092)
WARNING:__main__:Kafka is not ready yet; retrying consumer start (1/30)
```

After several retries the worker reached Kafka and joined the consumer group:

```text
INFO:aiokafka.consumer.group_coordinator:Joined group 'clickhouse-chat-writer'
INFO:__main__:ClickHouse consumer started
```

The Kafka exporter exited when it started before the Kafka broker was accepting connections:

```text
F0428 18:20:21.312972       1 kafka_exporter.go:901] Error Init Kafka Client: kafka: client has run out of available brokers to talk to: dial tcp 172.18.0.4:29092: connect: connection refused
```

### Diagnosis

`docker compose depends_on` only waited for containers to start. It did not wait for Kafka or ClickHouse to become ready. The worker had its own Kafka retry loop, but `kafka-exporter` failed fast and exited.

### Change

Added Docker healthchecks for Kafka and ClickHouse in `docker-compose.yml`.

Updated `backend`, `worker`, and `kafka-exporter` to depend on healthy Kafka/ClickHouse services.

Added `restart: unless-stopped` to `kafka-exporter` so a transient startup miss does not leave metrics permanently down.

### Verification

```bash
docker compose config --quiet
```

Result: passed.

## 2026-04-28 - ClickHouse Worker Crash After Consumer Start

### Symptoms

The ClickHouse worker started and joined the Kafka group, then crashed shortly afterward. The visible traceback ended at:

```text
File "/app/app/workers/clickhouse_consumer.py", line 101, in <module>
  asyncio.run(main())
```

The final exception line was not included in the captured log.

### Diagnosis

The worker parsed Kafka records and inserted ClickHouse batches without isolating bad records or transient insert failures. A malformed historical Kafka message, schema mismatch, invalid JSON payload, or ClickHouse insert error could bring down the process after the consumer joined the group.

### Change

Updated `backend/app/workers/clickhouse_consumer.py` so invalid Kafka records are logged and skipped instead of crashing the worker.

Changed ClickHouse insert failure handling so failed batches are not committed to Kafka. This lets Kafka replay the batch after the error clears instead of losing messages or terminating the process.

Added a regression test in `backend/tests/test_clickhouse_consumer.py` for valid and invalid Kafka payload parsing.

### Verification

```bash
cd backend
PYTHONPATH=. pytest
```

Result: 7 passed.

## Notes

When adding new entries, include:

- the exact error text
- when it happened
- the suspected or confirmed cause
- the file or config changed
- the verification command and result
