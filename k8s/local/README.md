# Local Kubernetes

These manifests run the project in Docker Desktop Kubernetes for production-behavior practice. Docker Compose remains the fastest day-to-day development path.

## Prerequisites

Enable Kubernetes in Docker Desktop.

Build the local application images into Docker Desktop's image store:

```bash
docker build -t twitch-analyze-backend:local ./backend
docker build -t twitch-analyze-frontend:local ./frontend
```

The local app images use `imagePullPolicy: Never`, so pods will not pull them from a registry.

## Configure

Edit `configmap.yaml` for non-secret settings such as `TWITCH_CHANNELS`.

Edit `secret.example.yaml` for local secret values before applying, or copy it to an untracked `secret.yaml` and apply that file instead.

## Apply

```bash
kubectl apply -f k8s/local/namespace.yaml
kubectl apply -f k8s/local/
kubectl -n twitch-analyze get pods
kubectl -n twitch-analyze get services
```

## Open

NodePort services:

- Frontend: http://localhost:30080
- Backend API: http://localhost:30000/docs
- Prometheus: http://localhost:30090
- Grafana: http://localhost:30300

Grafana local credentials:

```text
admin / admin
```

## Useful Commands

Watch rollout state:

```bash
kubectl -n twitch-analyze rollout status deployment/backend
kubectl -n twitch-analyze rollout status deployment/worker
kubectl -n twitch-analyze rollout status deployment/frontend
```

View logs:

```bash
kubectl -n twitch-analyze logs deployment/backend
kubectl -n twitch-analyze logs deployment/worker
kubectl -n twitch-analyze logs statefulset/kafka
kubectl -n twitch-analyze logs statefulset/clickhouse
```

Restart a workload after rebuilding an image:

```bash
kubectl -n twitch-analyze rollout restart deployment/backend
kubectl -n twitch-analyze rollout restart deployment/frontend
kubectl -n twitch-analyze rollout restart deployment/worker
```

Delete the local cluster resources:

```bash
kubectl delete namespace twitch-analyze
```

The Kafka and ClickHouse PersistentVolumeClaims belong to the namespace. Deleting the namespace deletes those local data volumes too.
