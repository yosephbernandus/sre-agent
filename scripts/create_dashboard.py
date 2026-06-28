"""Create the SRE On-Call Agent dashboard in Datadog (6 widgets + model template
variable). Run after the agent has emitted some metrics.

  python scripts/create_dashboard.py
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


def ts(title, q, display="line"):
    return {"definition": {"type": "timeseries", "title": title,
                           "requests": [{"q": q, "display_type": display}]}}


def qv(title, q, aggregator="sum"):
    return {"definition": {"type": "query_value", "title": title, "autoscale": True,
                           "requests": [{"q": q, "aggregator": aggregator}]}}


WIDGETS = [
    qv("Total cost (USD)", "sum:sre_agent.bedrock.cost_usd{$model}"),
    ts("Cost per request (USD)", "avg:sre_agent.bedrock.cost_usd{$model} by {model}"),
    ts("Latency ms (avg)", "avg:sre_agent.bedrock.latency_ms{$model} by {model}"),
    ts("Bedrock calls", "sum:sre_agent.bedrock.calls{$model}.as_count()", "bars"),
    ts("Grounding score (avg)", "avg:sre_agent.grounding_score{$model} by {domain}"),
    ts("Hallucination (withheld)", "sum:sre_agent.hallucination{$model}.as_count()", "bars"),
    qv("Guardrail blocks", "sum:sre_agent.guardrail_blocks{*}.as_count()"),
]

dashboard = {
    "title": "SRE On-Call Agent - Observable AI",
    "description": "Self-tuning observable SRE agent: cost, latency, grounding, guardrails.",
    "layout_type": "ordered",
    "widgets": WIDGETS,
    "template_variables": [{"name": "model", "prefix": "model", "default": "*"}],
}

res = datadog.api.Dashboard.create(**dashboard)
host = "app." + DD_SITE
print(f"Dashboard created: https://{host}{res.get('url', '/dashboard/lists')}")
