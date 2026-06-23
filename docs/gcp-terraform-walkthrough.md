# GCP Terraform Walkthrough

This document explains the Terraform deployment for the GCP version of the Twitch Analyze app.

The Terraform configuration creates a single Compute Engine VM, opens the required firewall access, installs Docker on boot, clones this repository, writes a production `.env`, and starts the app with Docker Compose.

## Main Files

- `infra/gcp/terraform/versions.tf`: Terraform and provider version requirements.
- `infra/gcp/terraform/variables.tf`: Inputs used by the deployment.
- `infra/gcp/terraform/main.tf`: GCP resources created by Terraform.
- `infra/gcp/terraform/outputs.tf`: Useful values printed after deployment.
- `infra/gcp/terraform/templates/startup.sh.tftpl`: VM startup script that installs and runs the app.

## 1. Terraform Version and Provider

`versions.tf` defines the minimum Terraform version and the Google Cloud provider:

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}
```

This does not create cloud resources. It controls which Terraform and Google provider versions are allowed.

## 2. Input Variables

`variables.tf` defines the values Terraform needs.

Important variables:

| Variable | Purpose |
| --- | --- |
| `project_id` | GCP project ID to deploy into. |
| `region` | GCP region, default `us-central1`. |
| `zone` | GCP zone, default `us-central1-a`. |
| `app_name` | Resource name prefix, default `twitch-analyze`. |
| `machine_type` | VM size, such as `e2-small` or `e2-medium`. |
| `boot_disk_size_gb` | Boot disk size, default `30`. |
| `domain` | Domain served by Caddy, such as `test.davidwu.com`. |
| `admin_ssh_cidr` | IP range allowed to SSH to the VM. |
| `repo_url` | Git repository cloned by the VM. |
| `repo_access_token` | Optional token for cloning a private repo. |
| `repo_ref` | Git branch or tag to deploy. |
| `twitch_channels` | Comma-separated Twitch channels to ingest. |
| `clickhouse_password` | ClickHouse password written to the app `.env`. |

Real values usually go in:

```text
infra/gcp/terraform/terraform.tfvars
```

Do not commit `terraform.tfvars` because it can contain secrets.

Variables marked with `sensitive = true` are hidden from normal Terraform CLI output, but they can still exist in Terraform state. Treat `terraform.tfstate` as sensitive too.

## 3. Google Provider

`main.tf` starts with:

```hcl
provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
```

This tells Terraform which GCP project, region, and zone to use.

The `var.name` syntax means Terraform reads the value from a variable.

## 4. VPC Network

```hcl
resource "google_compute_network" "app" {
  name                    = "${var.app_name}-network"
  auto_create_subnetworks = false
}
```

This creates a custom VPC network named something like:

```text
twitch-analyze-network
```

`auto_create_subnetworks = false` means GCP will not automatically create default subnets in every region.

## 5. Subnet

```hcl
resource "google_compute_subnetwork" "app" {
  name          = "${var.app_name}-subnet"
  ip_cidr_range = "10.20.0.0/24"
  region        = var.region
  network       = google_compute_network.app.id
}
```

This creates a subnet inside the custom VPC.

The subnet provides private IP addresses in this range:

```text
10.20.0.0/24
```

This line creates a dependency on the VPC:

```hcl
network = google_compute_network.app.id
```

Terraform sees that dependency and creates the network before the subnet.

## 6. Web Firewall Rule

```hcl
resource "google_compute_firewall" "web" {
  name    = "${var.app_name}-web"
  network = google_compute_network.app.name

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["${var.app_name}-web"]
}
```

This opens:

```text
80  HTTP
443 HTTPS
```

to the public internet.

Port `80` is needed for HTTP and Let's Encrypt validation. Port `443` is needed for HTTPS.

The rule only applies to VMs with this tag:

```hcl
target_tags = ["${var.app_name}-web"]
```

## 7. SSH Firewall Rule

```hcl
resource "google_compute_firewall" "ssh" {
  name    = "${var.app_name}-ssh"
  network = google_compute_network.app.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = [var.admin_ssh_cidr]
  target_tags   = ["${var.app_name}-ssh"]
}
```

This opens SSH only from the IP range configured in `admin_ssh_cidr`.

A good value looks like:

```hcl
admin_ssh_cidr = "YOUR_PUBLIC_IP/32"
```

`/32` means exactly one IP address.

Avoid this unless you intentionally want SSH exposed to the whole internet:

```hcl
admin_ssh_cidr = "0.0.0.0/0"
```

## 8. Compute Engine VM

```hcl
resource "google_compute_instance" "app" {
  name         = var.app_name
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["${var.app_name}-web", "${var.app_name}-ssh"]
```

This creates the VM.

The tags connect the VM to the firewall rules:

```text
twitch-analyze-web
twitch-analyze-ssh
```

Without those tags, the firewall rules would not apply to the VM.

## 9. Boot Disk

```hcl
boot_disk {
  initialize_params {
    image = "debian-cloud/debian-12"
    size  = var.boot_disk_size_gb
    type  = "pd-standard"
  }
}
```

This creates the VM boot disk.

It uses:

- Debian 12
- Standard persistent disk
- The configured disk size, default `30` GB

The disk stores the OS, Docker, cloned repo, Docker images, Docker volumes, ClickHouse data, and Kafka data.

Stopping the VM stops compute charges, but the disk can still incur storage cost.

## 10. Network Interface and External IP

```hcl
network_interface {
  subnetwork = google_compute_subnetwork.app.id

  access_config {
  }
}
```

This attaches the VM to the subnet.

The empty `access_config {}` tells GCP to assign an external public IP.

That public IP is what the DNS A record should point to:

```text
test.davidwu.com -> VM external IP
```

This IP is ephemeral unless the Terraform config is changed to reserve a static IP.

## 11. OS Login

```hcl
metadata = {
  enable-oslogin = "TRUE"
}
```

This enables GCP OS Login for SSH.

SSH access is managed through Google Cloud identity instead of manually placing SSH keys on the VM.

Example:

```bash
gcloud compute ssh twitch-analyze --zone us-central1-a
```

## 12. Startup Script Template

```hcl
metadata_startup_script = templatefile("${path.module}/templates/startup.sh.tftpl", {
  app_env              = var.app_env
  log_level            = var.log_level
  domain               = var.domain
  repo_url             = var.repo_url
  repo_username        = var.repo_username
  repo_access_token    = var.repo_access_token
  repo_ref             = var.repo_ref
  twitch_client_id     = var.twitch_client_id
  twitch_client_secret = var.twitch_client_secret
  twitch_access_token  = var.twitch_access_token
  twitch_refresh_token = var.twitch_refresh_token
  twitch_user_id       = var.twitch_user_id
  twitch_username      = var.twitch_username
  twitch_channels      = var.twitch_channels
  twitch_mode          = var.twitch_ingestion_mode
  clickhouse_password  = var.clickhouse_password
  recent_limit         = var.recent_message_limit
})
```

Terraform renders `startup.sh.tftpl` into a real shell script and injects the configured variable values.

For example, this template value:

```bash
APP_DOMAIN=${domain}
```

can become:

```bash
APP_DOMAIN=test.davidwu.com
```

The startup script runs automatically when the VM boots.

## 13. Startup Script: Install Docker

The script installs base packages:

```bash
apt-get update
apt-get install -y ca-certificates curl git gnupg
```

Then it installs Docker and Docker Compose:

```bash
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

The app runs through Docker Compose, so the VM only needs Docker plus the cloned repository.

## 14. Startup Script: Docker Download Limit

```bash
cat > /etc/docker/daemon.json <<'JSONEOF'
{
  "max-concurrent-downloads": 1
}
JSONEOF
```

This limits Docker to one image download at a time.

Small VMs can fail when Docker pulls too many image layers concurrently.

## 15. Startup Script: Swap

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
```

This creates a 2 GB swap file.

Swap is disk-backed memory. It can prevent crashes when RAM is tight, but it is much slower than real RAM.

This helps on `e2-small`, but `e2-medium` is still the more reliable minimum for the current Kafka, Zookeeper, ClickHouse, backend, worker, frontend, and Caddy stack.

## 16. Startup Script: Clone the Repository

The app directory on the VM is:

```text
/opt/twitch_analyze
```

If `repo_access_token` is set, the script builds an authenticated GitHub clone URL.

Then it clones or updates the repo:

```bash
git clone "$CLONE_URL" "$APP_DIR"
git -C "$APP_DIR" checkout "${repo_ref}"
git -C "$APP_DIR" pull --ff-only origin "${repo_ref}" || true
```

`repo_ref` controls which branch or tag is deployed.

## 17. Startup Script: Write Production .env

The script writes:

```text
/opt/twitch_analyze/.env
```

That file contains deployment configuration:

```env
APP_ENV=gcp
APP_DOMAIN=test.davidwu.com
TWITCH_CHANNELS=...
CLICKHOUSE_PASSWORD=...
CORS_ORIGINS=https://test.davidwu.com
```

The backend, worker, ClickHouse setup, and frontend proxy behavior use these values.

## 18. Startup Script: Low-Cost Compose Override

The script writes:

```text
/opt/twitch_analyze/docker-compose.gcp-lowcost.yml
```

This override reduces resource usage and disables nonessential local services.

It reduces memory for:

- Zookeeper
- Kafka
- ClickHouse
- Backend
- Worker
- Frontend
- Caddy

It disables:

- Kafka exporter
- Kafka Console
- Prometheus
- Grafana

That keeps the cloud deployment cheaper than the full local development stack.

## 19. Startup Script: Start Docker Compose

The deployment uses these compose files:

```bash
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gcp-lowcost.yml"
```

That means the final runtime config is merged from:

1. Base Compose stack
2. Production overrides
3. GCP low-cost overrides

The script validates the Compose config:

```bash
docker compose $COMPOSE_FILES config --quiet
```

Then starts the app:

```bash
docker compose $COMPOSE_FILES up -d --build
```

It retries startup several times because small VMs can fail during image pulls or builds.

## 20. VM Service Account

```hcl
service_account {
  scopes = ["cloud-platform"]
}
```

This gives the VM broad Google Cloud API scope.

The current app mainly runs Docker and does not need broad GCP API access, so this could be tightened later.

## 21. VM Scheduling

```hcl
scheduling {
  automatic_restart   = true
  on_host_maintenance = "MIGRATE"
  preemptible         = false
}
```

This means:

- GCP restarts the VM if it crashes.
- GCP migrates the VM during host maintenance.
- The VM is not preemptible or spot.

Spot VMs are cheaper but can be terminated by Google at any time.

## 22. Outputs

`outputs.tf` prints useful deployment values.

```hcl
output "external_ip" {
  description = "Ephemeral external IP address. Point your domain A record here."
  value       = google_compute_instance.app.network_interface[0].access_config[0].nat_ip
}
```

Use this IP in GoDaddy:

```text
Type: A
Name: test
Value: external_ip
```

The app URL output is:

```hcl
output "app_url" {
  value = "https://${var.domain}"
}
```

For example:

```text
https://test.davidwu.com
```

## 23. Terraform Command Flow

Initialize provider dependencies:

```bash
terraform -chdir=infra/gcp/terraform init
```

Validate syntax:

```bash
terraform -chdir=infra/gcp/terraform validate
```

Preview changes:

```bash
terraform -chdir=infra/gcp/terraform plan
```

Apply changes:

```bash
terraform -chdir=infra/gcp/terraform apply
```

Show outputs:

```bash
terraform -chdir=infra/gcp/terraform output
```

## 24. Request Flow

Once deployed, browser traffic flows like this:

```text
Browser
  -> test.davidwu.com
  -> GoDaddy DNS A record
  -> GCP VM external IP
  -> GCP firewall allows 80/443
  -> Caddy container
  -> frontend/backend containers
  -> backend talks to Kafka and ClickHouse
  -> worker consumes Kafka and writes to ClickHouse
```

Caddy is the public entry point.

Kafka, Zookeeper, ClickHouse, backend, and worker communicate privately inside Docker networking.

## 25. Mental Model

Terraform manages infrastructure:

```text
network
subnet
firewall
VM
disk
external IP
startup script
```

Docker Compose manages the application:

```text
frontend
backend
worker
Kafka
Zookeeper
ClickHouse
Caddy
```

The startup script connects those layers:

```text
Terraform creates VM
VM runs startup script
startup script installs Docker
Docker Compose starts app
```
