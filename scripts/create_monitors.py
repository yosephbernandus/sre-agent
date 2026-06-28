"""Create alerting monitor + SLOs for the SRE On-Call Agent (the "operate" pillar).

  python scripts/create_monitors.py

- hallucination_rate monitor: alert if too many answers withheld in 10m
- quality SLO (metric-based): >=80% answers grounded (30d)
- latency SLO (monitor-based): p95 latency <= 30s (30d)
"""

import os

import datadog
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "app.env"))
load_dotenv()

DD_SITE = os.environ.get("DD_SITE", "us5.datadoghq.com")
datadog.initialize(
    api_key=os.environ["DD_API_KEY"],
    app_key=os.environ["DD_APP_KEY"],
    api_host=f"https://api.{DD_SITE}",
)
HOST = "app." + DD_SITE


def _replace_monitor(name, **kwargs):
    for m in datadog.api.Monitor.get_all():
        if m.get("name") == name:
            datadog.api.Monitor.delete(m["id"])
    return datadog.api.Monitor.create(name=name, **kwargs)


# ── 1. Hallucination-rate monitor ────────────────────────────────────────
hall = _replace_monitor(
    "[SRE Agent] hallucination rate high",
    type="metric alert",
    query="sum(last_10m):sum:sre_agent.hallucination{*}.as_count() > 3",
    message="Agent withheld >3 answers in 10m (possible quality regression). @oncall review.",
    tags=["team:sre", "source:sre-agent"],
    options={"thresholds": {"critical": 3, "warning": 1}, "notify_no_data": False},
)
print(f"monitor (hallucination): id={hall.get('id')}")

# ── 2. Latency monitor (backs the latency SLO) ───────────────────────────
lat = _replace_monitor(
    "[SRE Agent] p95 latency high",
    type="metric alert",
    query="avg(last_5m):avg:sre_agent.bedrock.latency_ms{*} > 30000",
    message="Agent p95 response latency above 30s. @oncall investigate.",
    tags=["team:sre", "source:sre-agent"],
    options={"thresholds": {"critical": 30000}, "notify_no_data": False},
)
print(f"monitor (latency): id={lat.get('id')}")

# ── 3. Quality SLO (metric-based) ────────────────────────────────────────
try:
    quality = datadog.api.ServiceLevelObjective.create(
        type="metric",
        name="[SRE Agent] Answer quality (grounded)",
        description="≥80% of answers grounded (grounding score ≥ threshold, not withheld).",
        query={
            "numerator": "sum:sre_agent.answer.grounded{*}.as_count()",
            "denominator": "sum:sre_agent.answer.total{*}.as_count()",
        },
        thresholds=[{"timeframe": "30d", "target": 80.0}],
        tags=["team:sre", "source:sre-agent"],
    )
    print(f"SLO (quality): {quality}")
except Exception as exc:
    print(f"SLO (quality) failed: {exc}")

# ── 4. Latency SLO (monitor-based) ───────────────────────────────────────
try:
    lat_slo = datadog.api.ServiceLevelObjective.create(
        type="monitor",
        name="[SRE Agent] Latency p95 <= 30s",
        description="p95 response latency under 30s.",
        monitor_ids=[lat["id"]],
        thresholds=[{"timeframe": "30d", "target": 99.0}],
        tags=["team:sre", "source:sre-agent"],
    )
    print(f"SLO (latency): {lat_slo}")
except Exception as exc:
    print(f"SLO (latency) failed: {exc}")

print(f"\nDone. Monitors: https://{HOST}/monitors/manage  ·  SLOs: https://{HOST}/slo")
