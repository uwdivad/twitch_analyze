variable "project_id" {
  description = "GCP project ID to deploy into."
  type        = string
}

variable "region" {
  description = "GCP region. Use a Compute Engine Free Tier eligible US region."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone for the VM."
  type        = string
  default     = "us-central1-a"
}

variable "app_name" {
  description = "Name prefix for GCP resources."
  type        = string
  default     = "twitch-analyze"
}

variable "machine_type" {
  description = "Compute Engine machine type. e2-micro is the near-free default."
  type        = string
  default     = "e2-micro"
}

variable "boot_disk_size_gb" {
  description = "Boot disk size in GB. Keep at 30 GB to fit the Free Tier standard disk allowance."
  type        = number
  default     = 30
}

variable "domain" {
  description = "Domain name Caddy should serve. Point an A record at the VM external IP after apply."
  type        = string
}

variable "admin_ssh_cidr" {
  description = "CIDR allowed to SSH to the VM. Prefer your public IP with /32."
  type        = string
}

variable "repo_url" {
  description = "Git repository URL the VM should clone."
  type        = string
  default     = "https://github.com/uwdivad/twitch_analyze.git"
}

variable "repo_username" {
  description = "Username used when repo_access_token is set for HTTPS Git clones."
  type        = string
  default     = "x-access-token"
}

variable "repo_access_token" {
  description = "Optional token for cloning a private HTTPS Git repository."
  type        = string
  default     = ""
  sensitive   = true
}

variable "repo_ref" {
  description = "Git branch or tag to check out on the VM."
  type        = string
  default     = "main"
}

variable "app_env" {
  description = "Application environment value written to .env."
  type        = string
  default     = "gcp"
}

variable "log_level" {
  description = "Backend log level."
  type        = string
  default     = "INFO"
}

variable "twitch_channels" {
  description = "Comma-separated Twitch channel logins to ingest."
  type        = string
}

variable "twitch_ingestion_mode" {
  description = "Twitch ingestion mode. Keep irc for the lowest-cost deployment unless EventSub credentials are configured."
  type        = string
  default     = "irc"
}

variable "twitch_client_id" {
  description = "Twitch client ID for EventSub mode."
  type        = string
  default     = ""
}

variable "twitch_client_secret" {
  description = "Twitch client secret for EventSub mode."
  type        = string
  default     = ""
  sensitive   = true
}

variable "twitch_access_token" {
  description = "Twitch access token for authenticated IRC or EventSub mode."
  type        = string
  default     = ""
  sensitive   = true
}

variable "twitch_refresh_token" {
  description = "Twitch refresh token, if needed operationally."
  type        = string
  default     = ""
  sensitive   = true
}

variable "twitch_user_id" {
  description = "Twitch user ID for EventSub mode. If omitted, backend resolves it from the access token."
  type        = string
  default     = ""
}

variable "twitch_username" {
  description = "Twitch username for authenticated IRC mode."
  type        = string
  default     = ""
}

variable "clickhouse_password" {
  description = "ClickHouse default user password."
  type        = string
  sensitive   = true
}

variable "recent_message_limit" {
  description = "Number of recent messages retained in backend memory."
  type        = number
  default     = 500
}

variable "openai_api_key" {
  description = "OpenAI API key used for manual chat summary generation."
  type        = string
  default     = ""
  sensitive   = true
}

variable "openai_summary_model" {
  description = "OpenAI model used for manual chat summary generation."
  type        = string
  default     = "gpt-5.2"
}

variable "openai_summary_max_messages" {
  description = "Maximum sampled chat messages sent to OpenAI for one summary."
  type        = number
  default     = 250
}
