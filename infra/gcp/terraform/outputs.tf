output "instance_name" {
  description = "Compute Engine VM name."
  value       = google_compute_instance.app.name
}

output "instance_zone" {
  description = "Compute Engine VM zone."
  value       = google_compute_instance.app.zone
}

output "external_ip" {
  description = "Ephemeral external IP address. Point your domain A record here."
  value       = google_compute_instance.app.network_interface[0].access_config[0].nat_ip
}

output "app_url" {
  description = "HTTPS URL Caddy should serve after DNS points to the VM."
  value       = "https://${var.domain}"
}
