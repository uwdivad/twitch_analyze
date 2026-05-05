# AWS Deployment Guide

This guide describes the lowest-cost AWS path for a learning/staging deployment. It keeps the current technologies and runs the stack on one EC2 instance with Docker Compose.

## Target Architecture

```text
Internet
  -> EC2 security group ports 80/443
  -> Caddy reverse proxy
  -> React static frontend
  -> FastAPI backend
  -> Kafka -> ClickHouse worker -> ClickHouse
```

Kafka, ClickHouse, Prometheus, Grafana, and Redpanda Console run on the same host. Admin tools are bound to `127.0.0.1` and should be accessed with SSH tunnels.

## AWS Resources

- EC2: start with `t3.medium` for x86 image compatibility and 4 GB RAM.
- Storage: gp3 EBS volume, 30-50 GB to start.
- Security group:
  - allow `80` and `443` from the internet
  - allow `22` only from your IP
  - do not open Kafka, ClickHouse, Prometheus, Grafana, or Kafka Console publicly
- Optional DNS: point your domain or subdomain at the EC2 public IP.

## Instance Setup

Install Docker and a current Docker Compose plugin on the EC2 instance, then clone the repository. The production override uses Compose merge tags to replace local development ports and bind mounts.

Create `.env` from `.env.example` and set production values:

```bash
APP_ENV=aws
APP_DOMAIN=your-domain.example.com
CORS_ORIGINS=https://your-domain.example.com
TWITCH_CHANNELS=some_channel,another_channel
CLICKHOUSE_PASSWORD=replace-with-a-strong-password
```

If you do not have a domain yet, set:

```bash
APP_DOMAIN=:80
CORS_ORIGINS=http://your-ec2-public-ip
```

## Deploy

From the repository root:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Caddy exposes the application on ports `80` and `443`. It proxies `/api`, `/health`, and `/ws` to the backend and proxies all other paths to the production frontend container.

## Admin Access

Use SSH tunnels for local-only tools:

```bash
ssh -L 3000:127.0.0.1:3000 \
    -L 8080:127.0.0.1:8080 \
    -L 9090:127.0.0.1:9090 \
    ec2-user@your-ec2-host
```

Then open:

- Grafana: `http://localhost:3000`
- Kafka Console: `http://localhost:8080`
- Prometheus: `http://localhost:9090`

## Operations

Check services:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend worker
```

Update deployment:

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Backups:

- Take regular EBS snapshots.
- Kafka data is stored in the `kafka-data` Docker volume.
- ClickHouse data is stored in the `clickhouse-data` Docker volume.
- Caddy TLS data is stored in `caddy-data`.

## Scale-Up Path

This deployment is intentionally single-node and low-cost. When downtime or operational risk becomes unacceptable:

- move Kafka to Amazon MSK
- move ClickHouse to ClickHouse Cloud
- move app containers to EKS or ECS
- replace SSH-tunneled admin tools with authenticated internal access
