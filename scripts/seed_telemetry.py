"""Seed Datadog with realistic demo telemetry so the agent's MCP queries return
real evidence: logs (db/network/app), metrics (error counts, latency), and one
monitor that will go into ALERT. Run once before the demo.

  python scripts/seed_telemetry.py
"""

import os
import time

import datadog
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "app.env"))
load_dotenv()

DD_API_KEY = os.environ["DD_API_KEY"]
DD_APP_KEY = os.environ["DD_APP_KEY"]
DD_SITE = os.environ.get("DD_SITE", "us5.datadoghq.com")
API_HOST = f"https://api.{DD_SITE}"
LOGS_URL = f"https://http-intake.logs.{DD_SITE}/api/v2/logs"

datadog.initialize(api_key=DD_API_KEY, app_key=DD_APP_KEY, api_host=API_HOST)

# ── 1. Logs (varied domains) ──────────────────────────────────────────────
LOGS = [
    {"service": "payments-db", "ddtags": "domain:db,env:prod,host:db-prod-02",
     "message": "ERROR FATAL: remaining connection slots are reserved; connection pool exhausted (max=100 active=100)", "status": "error"},
    {"service": "payments-db", "ddtags": "domain:db,env:prod,host:db-prod-02",
     "message": "ERROR deadlock detected on relation 'transactions'; query cancelled", "status": "error"},
    {"service": "checkout", "ddtags": "domain:app,env:prod,host:web-prod-01",
     "message": "ERROR 500 Internal Server Error: upstream payments-db timeout after 5000ms", "status": "error"},
    {"service": "checkout", "ddtags": "domain:app,env:prod,host:web-prod-01",
     "message": "WARN CPU utilisation 95% sustained for 10m on web-prod-01", "status": "warn"},
    {"service": "edge-proxy", "ddtags": "domain:network,env:prod,host:lb-01",
     "message": "ERROR upstream connect timeout: dns resolution slow for payments-db.internal (3200ms)", "status": "error"},
    {"service": "edge-proxy", "ddtags": "domain:network,env:prod,host:lb-01",
     "message": "WARN 5xx error rate 12% over last 5m", "status": "warn"},
]


def send_logs() -> None:
    headers = {"DD-API-KEY": DD_API_KEY, "Content-Type": "application/json"}
    payload = [
        {"ddsource": "sre-demo", "service": l["service"], "ddtags": l["ddtags"],
         "hostname": l["ddtags"].split("host:")[-1], "message": l["message"], "status": l["status"]}
        for l in LOGS
    ]
    r = requests.post(LOGS_URL, json=payload, headers=headers, timeout=20)
    print(f"logs: HTTP {r.status_code} ({len(payload)} lines)")


# ── 2. Metrics ─────────────────────────────────────────────────────────────
def send_metrics() -> None:
    now = int(time.time())
    series = [
        {"metric": "sre_demo.db.errors", "points": [(now, 57)], "type": "gauge", "tags": ["service:payments-db", "domain:db"]},
        {"metric": "sre_demo.app.error_rate", "points": [(now, 12.0)], "type": "gauge", "tags": ["service:checkout", "domain:app"]},
        {"metric": "sre_demo.net.latency_ms", "points": [(now, 3200)], "type": "gauge", "tags": ["service:edge-proxy", "domain:network"]},
        {"metric": "sre_demo.host.cpu", "points": [(now, 95)], "type": "gauge", "tags": ["host:web-prod-01", "domain:infra"]},
    ]
    datadog.api.Metric.send(series)
    print(f"metrics: sent {len(series)} series")


# ── 3. Monitor that will ALERT ──────────────────────────────────────────────
def create_alerting_monitor() -> None:
    name = "[SRE Demo] payments-db error count high"
    existing = [m for m in datadog.api.Monitor.get_all() if m.get("name") == name]
    for m in existing:
        datadog.api.Monitor.delete(m["id"])
    mon = datadog.api.Monitor.create(
        type="metric alert",
        query="avg(last_5m):sum:sre_demo.db.errors{*} > 10",
        name=name,
        message="payments-db error count is critically high. @oncall investigate connection pool. @webhook-codecraft-triage",
        tags=["domain:db", "team:sre", "source:sre-demo"],
        options={"thresholds": {"critical": 10, "warning": 5}, "notify_no_data": False},
    )
    print(f"monitor: created id={mon.get('id')} (will go ALERT once metric is evaluated)")


if __name__ == "__main__":
    send_logs()
    send_metrics()
    create_alerting_monitor()
    print("\nSeed complete. Logs/metrics queryable in ~30-60s; monitor alerts shortly after.")
