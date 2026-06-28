"""Auto-triage (Point C): turn a Datadog monitor alert into an investigation.

A Datadog webhook POSTs the alert here; we build an incident prompt, run the
agent (so it investigates via MCP + firewall + records to the ops-memory wiki),
and post the triage to Slack. Advisory + read-only - never changes the system.
"""

from __future__ import annotations

import logging

import requests

from app import agent
from app.config import Config
from app.router import ModelRouter

logger = logging.getLogger(__name__)


def _extract(payload: dict) -> dict:
    """Be lenient about the webhook shape - pull what we can."""
    g = lambda *ks: next((str(payload[k]) for k in ks if payload.get(k)), "")
    return {
        "title": g("title", "alert_title", "event_title") or "Datadog alert",
        "body": g("body", "message", "alert_message", "text"),
        "service": g("service", "scope"),
        "query": g("query", "alert_query"),
        "tags": g("tags"),
        "priority": g("priority", "alert_priority"),
        "link": g("link", "url", "event_url"),  # Datadog $LINK -> the alert event page
    }


def _service(alert: dict) -> str:
    """Best-effort service name from the alert (field or service:* tag)."""
    if alert["service"]:
        return alert["service"].split(",")[0].replace("service:", "").strip()
    for tag in alert["tags"].split(","):
        if tag.strip().startswith("service:"):
            return tag.split("service:")[-1].strip()
    return ""


def _dd_links(config: Config, alert: dict) -> str:
    """Slack-formatted Datadog links so on-call can jump straight to evidence."""
    host = "app." + config.dd_site
    links = []
    if alert["link"]:
        links.append(f"<{alert['link']}|Open alert>")
    svc = _service(alert)
    if svc:
        links.append(f"<https://{host}/logs?query=service:{svc}|Logs: {svc}>")
    links.append(f"<https://{host}/llm/traces|Agent traces>")
    return " · ".join(links)


def _incident_prompt(a: dict) -> str:
    parts = [f"ALERT: {a['title']}."]
    if a["body"]:
        parts.append(a["body"])
    if a["service"]:
        parts.append(f"Affected scope/service: {a['service']}.")
    if a["query"]:
        parts.append(f"Monitor query: {a['query']}.")
    parts.append(
        "Investigate using the Datadog logs/metrics/monitors tools and give "
        "SEVERITY (P1-P4), the root cause grounded in the evidence, and one "
        "concrete next step."
    )
    return " ".join(parts)


def post_slack(config: Config, alert: dict, result) -> bool:
    if not config.slack_webhook_url:
        return False
    status = (
        ":no_entry: WITHHELD - escalated to human (ungrounded)"
        if result.withheld
        else f"grounding {result.eval_score}"
    )
    tools = ", ".join(t.replace("search_datadog_", "") for t in result.tools_used) or "none"
    text = (
        f":rotating_light: *CodeCraft Auto-Triage* - {alert['title']}\n"
        f"{result.display_text}\n"
        f"_{status} · domain {result.domain} · {result.model.split('.')[-1]} · "
        f"{result.latency_ms}ms · tools: {tools}_\n"
        f":mag: {_dd_links(config, alert)}"
    )
    try:
        r = requests.post(config.slack_webhook_url, json={"text": text}, timeout=10)
        return r.status_code < 300
    except Exception as exc:
        logger.warning("slack post failed: %s", exc)
        return False


def triage(payload: dict, config: Config, router: ModelRouter):
    """Run one auto-triage. Returns (alert, AgentResult, slack_posted)."""
    alert = _extract(payload)
    prompt = _incident_prompt(alert)
    logger.info("auto-triage: %s", alert["title"])
    result = agent.run_turn(prompt, [], config, router)  # fresh, independent history
    slacked = post_slack(config, alert, result)
    return alert, result, slacked
