# Kubernetes Roadmap

This project should keep Docker Compose as the fastest local development path while adding Kubernetes as a production-behavior learning track.

The goal is not just to run containers in a cluster. The goal is to practice the operational model Kubernetes introduces: reconciliation, automatic restarts, service discovery, readiness/liveness probes, config separation, persistent workloads, and rolling deploys.

## Why Kubernetes Here

Kubernetes is useful for this project because the stack has the same moving parts that appear in production data systems:

- stateless HTTP services
- long-running background workers
- stateful infrastructure
- internal service-to-service networking
- secrets and environment configuration
- health checks and automatic restarts
- metrics and dashboards

For simple local restarts, Docker Compose `restart` policies are enough. Kubernetes is worth adding because learning production-style behavior is an explicit project goal.

## Recommended Phases

### Phase 1 - Local Cluster Baseline

Use Docker Desktop Kubernetes.

Status: implemented as plain manifests in `k8s/local/`.

Use plain manifests before introducing Helm or Kustomize.

Initial files:

```text
k8s/
  local/
    namespace.yaml
    configmap.yaml
    secret.example.yaml
    kafka.yaml
    clickhouse-init-configmap.yaml
    clickhouse.yaml
    backend.yaml
    worker.yaml
    frontend.yaml
    monitoring.yaml
    README.md
```

First milestone:

- Kafka runs in the cluster.
- ClickHouse runs in the cluster.
- Backend can connect to Kafka and ClickHouse through Kubernetes Services.
- Worker consumes Kafka messages and writes to ClickHouse.
- Frontend can call the backend.

### Phase 2 - Health And Restart Behavior

Add production-style health behavior:

- backend readiness probe
- backend liveness probe
- worker liveness probe or metrics-based health endpoint
- Kafka readiness check
- ClickHouse readiness check
- restart behavior through Deployments and pod restart policy

Expected learning:

- a pod can be running but not ready
- Kubernetes restarts crashed containers
- Services route only to ready pods
- bad probes can cause restart loops

### Phase 3 - Configuration And Secrets

Move non-secret settings into ConfigMaps:

- Kafka bootstrap servers
- Kafka topic
- ClickHouse host/port/database
- app environment
- CORS origins

Move sensitive values into Secrets:

- ClickHouse password
- Twitch client secret
- Twitch access/refresh tokens

Keep `secret.example.yaml` committed, but do not commit real secrets.

### Phase 4 - Stateful Workloads

Model Kafka and ClickHouse as StatefulSets with persistent volumes.

For learning, a single-broker Kafka StatefulSet is acceptable. For real production, Kafka should eventually move to an operator such as Strimzi.

ClickHouse should use a PersistentVolumeClaim so chat history survives pod replacement.

Expected learning:

- Deployments are a poor fit for durable state
- StatefulSets provide stable pod identities
- PVCs decouple pod lifecycle from data lifecycle
- deleting a pod is different from deleting its volume

### Phase 5 - Monitoring In Cluster

Run Prometheus and Grafana inside Kubernetes.

Expose:

- FastAPI backend metrics
- ClickHouse worker metrics
- Kafka exporter metrics

Expected learning:

- scrape targets move from Compose service names to Kubernetes Services
- Grafana dashboards can be provisioned with ConfigMaps
- exporter restarts and readiness affect observability

### Phase 6 - Deployment Workflow

Practice image build and rollout behavior:

- build local images
- load or reference images in Docker Desktop Kubernetes
- use image tags instead of `latest` once the workflow stabilizes
- run `kubectl rollout status`
- test rollback with `kubectl rollout undo`

Expected learning:

- Kubernetes deploys a desired state
- updates are rollouts, not manual container restarts
- image tags and pull policy affect what code actually runs

## Service Mapping

| Compose service | Kubernetes shape | Notes |
| --- | --- | --- |
| `backend` | Deployment + Service | Stateless HTTP API. Add readiness/liveness probes. |
| `worker` | Deployment | Background consumer. No public Service required unless exposing metrics separately. |
| `frontend` | Deployment + Service | Vite/dev server for local cluster first; production image later. |
| `kafka` | StatefulSet + Service | Single broker for learning. Consider Strimzi later. |
| `clickhouse` | StatefulSet + Service + PVC | Persistent analytical storage. |
| `kafka-exporter` | Deployment + Service | Scraped by Prometheus. |
| `prometheus` | Deployment + Service | Later can use a PVC for metric retention. |
| `grafana` | Deployment + Service | Provision dashboards from ConfigMaps. |

## Local Development Rule

Docker Compose remains the default for fast iteration:

```bash
docker compose up --build
```

Kubernetes is the production-behavior track:

```bash
kubectl apply -f k8s/local/
```

This keeps day-to-day development simple while still building Kubernetes understanding deliberately.
