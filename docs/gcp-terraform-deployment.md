# GCP Terraform Deployment

This guide describes the lowest-cost Google Cloud Platform path for an experimental deployment. It keeps the current app architecture and runs Docker Compose on one Compute Engine VM.

## Target Architecture

```text
Internet
  -> Compute Engine firewall ports 80/443
  -> Caddy reverse proxy
  -> React static frontend
  -> FastAPI backend
  -> Kafka -> ClickHouse worker -> ClickHouse
```

The Terraform startup script disables Prometheus, Grafana, Kafka Exporter, and Kafka Console for this deployment to reduce memory use.

## Cost Model

The default Terraform variables target the Compute Engine Free Tier shape:

- VM: one non-preemptible `e2-micro`
- Region: an eligible US region such as `us-central1`
- Disk: 30 GB `pd-standard`
- Networking: ephemeral external IP, no load balancer, no NAT gateway, no Cloud DNS

This is intentionally a near-free experiment. Kafka, Zookeeper, ClickHouse, image builds, and the app all share a 1 GB VM, so startup can be slow and memory pressure is expected. The startup script adds swap to improve survivability.

## Prerequisites

Install and authenticate local tools:

```bash
gcloud auth application-default login
gcloud config set project your-gcp-project-id
gcloud services enable compute.googleapis.com
```

Create a local Terraform variables file:

```bash
cd infra/gcp/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
project_id       = "your-gcp-project-id"
domain           = "your-domain.example.com"
admin_ssh_cidr   = "your-public-ip/32"
twitch_channels  = "some_channel"
clickhouse_password = "replace-with-a-strong-password"
```

For lowest cost, use anonymous IRC mode by leaving Twitch credentials blank and setting only `twitch_channels`.

If the GitHub repository is private, create a fine-grained GitHub token with access to only this repo and set repository permission `Contents: Read-only`. Git clone requires contents access; metadata-only access is not enough. Add the token locally:

```hcl
repo_access_token = "github_pat_or_fine_grained_token"
```

Manual LLM chat summaries are optional. To enable them, add an OpenAI API key to local `terraform.tfvars`:

```hcl
openai_api_key = "sk-proj-your-openai-api-key"
openai_summary_model = "gpt-5.2"
openai_summary_max_messages = 250
```

If `openai_api_key` is blank, the dashboard still works, but summary generation returns a configuration error.

## Deploy

From `infra/gcp/terraform`:

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply
```

After apply, copy the `external_ip` output and create an `A` record for your domain pointing to that IP. Caddy will request HTTPS certificates after DNS resolves to the VM.

## Operate

SSH through OS Login:

```bash
gcloud compute ssh twitch-analyze --zone us-central1-a
```

Check the stack:

```bash
cd /opt/twitch_analyze
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gcp-lowcost.yml ps
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gcp-lowcost.yml logs -f backend worker caddy
curl http://localhost/health
```

Update the app after pushing new commits:

```bash
cd /opt/twitch_analyze
sudo git pull --ff-only origin main
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gcp-lowcost.yml up -d --build
```

Destroy everything when finished:

```bash
terraform destroy
```

## Important Notes

- Terraform uses local state by default. Do not commit `terraform.tfvars` or `.tfstate` files.
- Secrets are rendered into Terraform state, VM metadata, Git remote config, and `/opt/twitch_analyze/.env` on the VM. This is acceptable for a low-cost experiment, not a hardened production setup.
- The VM uses an ephemeral external IP. Recreating the VM may require updating DNS.
- If the stack cannot stay healthy on `e2-micro`, the smallest practical upgrade is changing `machine_type` to `e2-small` or `e2-medium`.
