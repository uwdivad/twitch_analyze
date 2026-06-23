provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

resource "google_compute_network" "app" {
  name                    = "${var.app_name}-network"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "app" {
  name          = "${var.app_name}-subnet"
  ip_cidr_range = "10.20.0.0/24"
  region        = var.region
  network       = google_compute_network.app.id
}

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

resource "google_compute_instance" "app" {
  name         = var.app_name
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["${var.app_name}-web", "${var.app_name}-ssh"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.boot_disk_size_gb
      type  = "pd-standard"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.app.id

    access_config {
    }
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

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
    openai_api_key       = var.openai_api_key
    openai_model         = var.openai_summary_model
    openai_max_messages  = var.openai_summary_max_messages
  })

  service_account {
    scopes = ["cloud-platform"]
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    preemptible         = false
  }
}
