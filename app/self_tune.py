"""Self-tune workflow: the agent reads its own performance and recommends a
model-routing change, which a human can then apply.

The recommendation is computed from the rolling in-memory turn log (reliable),
while a Datadog MCP metrics query is also issued so the agent demonstrably reads
its *own* observability (the "observe → understand → operate" loop). Decorated
`@workflow` so it shows as its own trace.
"""

from __future__ import annotations

import logging

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs.decorators import workflow

from app import state
from app.config import Config
from app.mcp_client import DatadogMCPClient, MCPClientError
from app.router import ModelRouter

logger = logging.getLogger(__name__)

_TRIGGERS = ("analyze my performance", "analyze performance", "self-tune", "self tune", "analyze")
_CHEAP = {"us.amazon.nova-micro-v1:0", "us.amazon.nova-lite-v1:0"}
_UPGRADE_TO = "us.amazon.nova-pro-v1:0"
_NARRATE_MODEL = "us.amazon.nova-lite-v1:0"


def _narrate(config: Config, facts: str) -> str:
    """LLM writes a 1-2 sentence explanation of the (already-decided) analysis.

    The decision + numbers are computed deterministically; the model only
    explains them - it must not change any number or decision. Fails soft.
    """
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=config.aws_region)
        prompt = (
            "You are an SRE platform analyst. Below is a deterministic performance "
            "analysis of an AI agent and the routing decision already made. In 1-2 "
            "sentences, explain to the on-call engineer what's happening and why the "
            "decision makes sense. Use ONLY the numbers/decision below - do not invent "
            "or change anything.\n\n" + facts
        )
        resp = client.converse(
            modelId=_NARRATE_MODEL,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 120, "temperature": 0.3},
        )
        return "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()
    except Exception as exc:
        logger.debug("narrate failed: %s", exc)
        return ""


def is_self_tune_trigger(lowered_message: str) -> bool:
    return any(t in lowered_message for t in _TRIGGERS)


def _aggregate() -> dict[str, dict]:
    """Group recent turns by domain → {n, withheld, avg_score, model}."""
    by_domain: dict[str, dict] = {}
    for t in state.RECENT_TURNS:
        d = by_domain.setdefault(
            t["domain"], {"n": 0, "withheld": 0, "score_sum": 0.0, "score_n": 0, "model": t["model"]}
        )
        d["n"] += 1
        d["model"] = t["model"]
        if t["withheld"]:
            d["withheld"] += 1
        if t["score"] is not None:
            d["score_sum"] += t["score"]
            d["score_n"] += 1
    for d in by_domain.values():
        d["avg_score"] = (d["score_sum"] / d["score_n"]) if d["score_n"] else None
    return by_domain


@workflow(name="self_tune_analyze")
def analyze_performance(config: Config, mcp_client: DatadogMCPClient | None) -> str:
    """Analyze own performance and produce a routing recommendation."""
    # Demonstrate self-observability: query own metrics via Datadog MCP.
    mcp_note = ""
    if mcp_client is not None:
        try:
            res = mcp_client.call_tool(
                "search_datadog_metrics", {"query": "sre_agent.hallucination"}
            )
            LLMObs.annotate(input_data="sre_agent.hallucination", output_data=res or "(none)")
            mcp_note = "\n(Read own metrics via Datadog MCP.)"
        except MCPClientError as exc:
            logger.debug("self-tune MCP query failed: %s", exc)

    by_domain = _aggregate()
    if not by_domain:
        state.set_recommendation(None)
        return ("Not enough data yet - investigate a few incidents first "
                "(including some that get withheld), then ask me to analyze again." + mcp_note)

    # Pick the worst domain currently on a cheap model.
    def badness(item):
        _domain, d = item
        rate = d["withheld"] / d["n"] if d["n"] else 0
        score = d["avg_score"] if d["avg_score"] is not None else 1.0
        return (rate, 1 - score)

    worst_domain, worst = max(by_domain.items(), key=badness)
    rate = worst["withheld"] / worst["n"] if worst["n"] else 0
    avg = worst["avg_score"]

    lines = ["Performance analysis (recent turns):"]
    for dom, d in sorted(by_domain.items(), key=lambda kv: kv[0]):
        avg_s = f"{d['avg_score']:.2f}" if d["avg_score"] is not None else "n/a"
        lines.append(f"  • {dom}: {d['n']} turns · withheld {d['withheld']}/{d['n']} · avg grounding {avg_s} · model {_short(d['model'])}")

    needs_fix = (rate > 0 or (avg is not None and avg < config.firewall_threshold)) and worst["model"] in _CHEAP
    if needs_fix:
        state.set_recommendation(
            {"domain": worst_domain, "from_model": worst["model"], "to_model": _UPGRADE_TO,
             "reason": f"withheld {worst['withheld']}/{worst['n']}, avg grounding {avg if avg is None else round(avg,2)}"}
        )
        lines.append("")
        lines.append(
            f"Recommendation: route {worst_domain} incidents from {_short(worst['model'])} "
            f"to {_short(_UPGRADE_TO)} (hallucinating: {worst['withheld']}/{worst['n']} withheld)."
        )
        lines.append('Reply "apply" to update routing.')
    else:
        state.set_recommendation(None)
        lines.append("")
        lines.append("No routing change needed - grounding is healthy across domains.")

    facts = "\n".join(lines)
    note = _narrate(config, facts)
    body = (note + "\n\n" + facts) if note else facts
    return body + mcp_note


def apply_last_recommendation(router: ModelRouter) -> str:
    """Apply the last recommendation to the router, if any."""
    rec = state.LAST_RECOMMENDATION
    if not rec:
        return "Nothing to apply - run an analysis first (\"analyze my performance\")."
    router.apply_recommendation(rec["domain"], rec["to_model"])
    state.set_recommendation(None)
    return (f"Applied: {rec['domain']} incidents now route to {_short(rec['to_model'])}. "
            f"Next {rec['domain']} incident will use the stronger model.")


def _short(model_id: str) -> str:
    return model_id.split(".")[-1].split("-v")[0]
