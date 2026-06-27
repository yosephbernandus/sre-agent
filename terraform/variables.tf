variable "datadog_api_key" {
  description = "Datadog API key (us5)"
  sensitive   = true
}

variable "datadog_app_key" {
  description = "Datadog Application key (us5, needs dashboards_write)"
  sensitive   = true
}

variable "datadog_api_url" {
  description = "Datadog API URL for the org's site"
  default     = "https://api.us5.datadoghq.com/"
}

variable "ml_app" {
  description = "Default LLM Obs ML app to scope the dashboard to"
  default     = "sre-oncall-agent"
}
