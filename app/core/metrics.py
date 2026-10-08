"""Prometheus metrics. Importing this module is the single integration point; an
OpenTelemetry exporter can be attached in ``app.main.create_app`` without touching callers."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("s4c_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "s4c_http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)

PROVIDER_REQUESTS = Counter(
    "s4c_provider_requests_total", "Price provider calls", ["provider", "operation", "outcome"]
)
PROVIDER_LATENCY = Histogram(
    "s4c_provider_request_duration_seconds", "Price provider latency", ["provider", "operation"]
)
PROVIDER_CACHE = Counter(
    "s4c_provider_cache_total", "Provider cache lookups", ["operation", "result"]
)
PROVIDER_RATE_LIMITED = Counter(
    "s4c_provider_rate_limited_total", "Provider 429 responses", ["provider", "operation"]
)

JOB_RUNS = Counter("s4c_job_runs_total", "Background job runs", ["job", "outcome"])
JOB_DURATION = Histogram("s4c_job_duration_seconds", "Background job duration", ["job"])
JOB_LAG = Gauge("s4c_job_lag_seconds", "Age of the oldest pending unit of work", ["job"])

FORECASTS = Counter("s4c_forecasts_total", "Forecasts generated", ["method"])
INFERENCE_LATENCY = Histogram("s4c_inference_duration_seconds", "Forecast inference", ["method"])
MODEL_FALLBACKS = Counter("s4c_model_fallbacks_total", "ML to baseline fallbacks", ["reason"])

ALERTS_CREATED = Counter("s4c_alerts_created_total", "Outbox rows created", ["trigger", "channel"])
ALERTS_SUPPRESSED = Counter(
    "s4c_alerts_suppressed_total", "Alerts suppressed", ["trigger", "reason"]
)
NOTIFICATION_DELIVERIES = Counter(
    "s4c_notification_deliveries_total", "Notification delivery attempts", ["channel", "outcome"]
)
RATE_LIMIT_REJECTIONS = Counter(
    "s4c_rate_limit_rejections_total", "Requests rejected by API rate limits", ["scope"]
)
