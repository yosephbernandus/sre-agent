"""Agent core: Bedrock converse loop with MCP tool dispatch, guardrails,
firewall, model routing, and self-tune handling. Instrumented with ddtrace
LLM Observability (@agent root span, @tool spans; Bedrock llm spans are
auto-instrumented)."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import boto3
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs.decorators import agent, tool

from app import memory, self_tune, state
from app.config import Config
from app.firewall import ESCALATION_MESSAGE, evaluate_and_gate
from app.guardrails import build_guardrail_config, check_input
from app.mcp_client import ALLOWED_TOOLS, DatadogMCPClient, MCPClientError
from app.metrics import emit_turn_metrics
from app.router import ModelRouter

logger = logging.getLogger(__name__)

# Per-1K-token pricing (USD). Fallback to Micro pricing for unknown models.
_PRICING = {
    "us.amazon.nova-micro-v1:0": (0.000035, 0.00014),
    "us.amazon.nova-lite-v1:0": (0.00006, 0.00024),
    "us.amazon.nova-pro-v1:0": (0.0008, 0.0032),
}
_DEFAULT_PRICING = (0.000035, 0.00014)

# Keyword → domain classification
_DOMAIN_KEYWORDS = {
    "db": ("database", "db", "postgres", "mysql", "rds", "query", "deadlock", "connection pool"),
    "network": ("network", "dns", "timeout", "latency", "packet", "load balancer", "elb", "connectivity"),
    "app": ("app", "service", "5xx", "exception", "crash", "error rate", "deploy", "payment", "checkout"),
    "infra": ("cpu", "memory", "disk", "host", "node", "kubernetes", "k8s", "pod", "ec2", "scaling"),
}

_SYSTEM = [
    {
        "text": (
            "You are an on-call SRE assistant for a production system. "
            "Investigate the incident the engineer describes. ALWAYS use the Datadog "
            "tools to gather live evidence (logs, metrics, monitors) before drawing a "
            "conclusion — do not invent a root cause. Be concise: give SEVERITY (P1-P4), "
            "the most likely root cause grounded in what the tools returned, and one "
            "concrete next step. If the tools return no relevant evidence, say so plainly "
            "instead of guessing."
        )
    }
]

# Simple, reliable tool surface mapped onto the allowed read-only MCP tools.
_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": name,
                "description": desc,
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Datadog search query"}
                        },
                        "required": ["query"],
                    }
                },
            }
        }
        for name, desc in (
            ("search_datadog_logs", "Search recent Datadog logs, e.g. 'service:payments status:error'."),
            ("search_datadog_metrics", "Search Datadog metrics by name/tag, e.g. 'system.cpu'."),
            ("search_datadog_monitors", "List/search Datadog monitors and their alert status, e.g. 'status:alert'."),
        )
    ]
}


@dataclass
class AgentResult:
    display_text: str
    kind: str = "answer"  # answer | self_tune | control | blocked
    withheld: bool = False
    eval_score: float | None = None
    model: str = ""
    domain: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    bedrock_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    guardrail_blocked: bool = False
    routing_table: dict[str, str] | None = None
    recalled: int = 0  # # of prior incidents recalled from ops memory


def classify_domain(message: str) -> str:
    """Keyword-based domain classification."""
    lowered = message.lower()
    best, best_hits = "default", 0
    for domain, words in _DOMAIN_KEYWORDS.items():
        hits = sum(1 for w in words if w in lowered)
        if hits > best_hits:
            best, best_hits = domain, hits
    return best


@tool
def dispatch_tool(client: DatadogMCPClient, name: str, query: str) -> str:
    """Execute a read-only Datadog MCP tool and return its text result."""
    LLMObs.annotate(input_data=query, tags={"tool.name": name, "tool.source": "datadog_mcp"})
    args: dict = {"query": query or "*"}
    if name == "search_datadog_logs":
        args["limit"] = 10
    try:
        output = client.call_tool(name, args)
    except MCPClientError as exc:
        output = f"(tool error: {exc})"
    LLMObs.annotate(output_data=output)
    return output


def _cost(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = _PRICING.get(model, _DEFAULT_PRICING)
    return tokens_in / 1000 * pin + tokens_out / 1000 * pout


@agent(name="oncall_turn")
def run_turn(
    user_message: str,
    history: list[dict],
    config: Config,
    router: ModelRouter,
) -> AgentResult:
    """Handle one user turn end-to-end."""
    text = user_message.strip()
    lowered = text.lower()

    # ── control: apply last self-tune recommendation ──────────────────────
    if lowered in ("apply", "apply patch", "apply recommendation") or lowered.startswith("apply "):
        msg = self_tune.apply_last_recommendation(router)
        return AgentResult(display_text=msg, kind="control", routing_table=router.get_routing_table())

    # ── self-tune trigger ─────────────────────────────────────────────────
    if self_tune.is_self_tune_trigger(lowered):
        client = DatadogMCPClient(config)
        try:
            client.initialize()
        except MCPClientError as exc:
            logger.warning("MCP init failed for self-tune: %s", exc)
        rec_text = self_tune.analyze_performance(config, client)
        return AgentResult(display_text=rec_text, kind="self_tune",
                           routing_table=router.get_routing_table())

    # ── input guardrail ───────────────────────────────────────────────────
    guard = check_input(text, config)
    if guard.blocked:
        emit_turn_metrics(config, cost_usd=0.0, latency_ms=0.0, model="-",
                          domain="-", bedrock_calls=0, guardrail_blocked=True, is_answer=False)
        return AgentResult(display_text=guard.reason, kind="blocked", guardrail_blocked=True)
    safe_message = guard.sanitized or text

    # ── normal investigation ──────────────────────────────────────────────
    domain = classify_domain(safe_message)
    model = router.get_model(domain)
    LLMObs.annotate(input_data=safe_message, tags={"domain": domain, "model": model})

    client = DatadogMCPClient(config)
    try:
        client.initialize()
    except MCPClientError as exc:
        logger.warning("MCP init failed: %s", exc)

    # Ops Memory recall (LLM-Wiki): pull similar past incidents as context.
    prior = memory.recall(domain, safe_message)
    recalled_n = prior.count("\n## ") + (1 if prior.startswith("## ") else 0) if prior else 0
    user_text = safe_message
    if prior:
        user_text += (
            "\n\n[Prior incidents from ops memory — reuse the resolution if this matches]\n" + prior
        )
    messages = list(history) + [{"role": "user", "content": [{"text": user_text}]}]
    bedrock = boto3.client("bedrock-runtime", region_name=config.aws_region)
    guardrail_cfg = build_guardrail_config(config)

    total_in = total_out = calls = 0
    tool_rounds = 0
    TOOL_BUDGET = 4  # cap evidence-gathering rounds so the model can't loop forever
    tools_used: list[str] = []
    evidence_parts: list[str] = []
    answer = ""
    t0 = time.perf_counter()

    for _ in range(config.max_iterations):
        # Force a tool call on the first turn so the agent always gathers
        # evidence before answering (Nova Micro/Lite otherwise sometimes skips
        # tools and answers from nothing → firewall withholds). Later turns use
        # auto so the model can actually finish.
        force_tool = calls == 0
        tool_config = (
            {**_TOOL_CONFIG, "toolChoice": {"any": {}}} if force_tool else _TOOL_CONFIG
        )
        kwargs = dict(
            modelId=model,
            messages=messages,
            system=_SYSTEM,
            toolConfig=tool_config,
            inferenceConfig={"maxTokens": 600, "temperature": 0.2},
        )
        if guardrail_cfg:
            kwargs["guardrailConfig"] = guardrail_cfg
        try:
            resp = bedrock.converse(**kwargs)
        except Exception as exc:
            # some models reject toolChoice:any — retry with auto
            if force_tool:
                logger.warning("toolChoice=any rejected (%s); retrying auto", exc)
                kwargs["toolConfig"] = _TOOL_CONFIG
                resp = bedrock.converse(**kwargs)
            else:
                raise
        calls += 1
        usage = resp.get("usage", {})
        total_in += usage.get("inputTokens", 0)
        total_out += usage.get("outputTokens", 0)

        out_msg = resp["output"]["message"]
        messages.append(out_msg)

        if resp.get("stopReason") == "tool_use":
            budget_left = tool_rounds < TOOL_BUDGET
            tool_rounds += 1
            results = []
            for block in out_msg["content"]:
                if "toolUse" not in block:
                    continue
                tu = block["toolUse"]
                name = tu["name"]
                if not budget_left:
                    payload = (
                        "Evidence budget reached. Do NOT call more tools. Give your final "
                        "answer now: SEVERITY (P1-P4), the root cause grounded in the evidence "
                        "already gathered above, and one concrete next step."
                    )
                elif name not in ALLOWED_TOOLS:
                    payload = f"(tool {name} not permitted)"
                else:
                    tools_used.append(name)
                    payload = dispatch_tool(client, name, tu["input"].get("query", "*"))
                    evidence_parts.append(f"[{name}] {payload}")
                results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [{"text": payload}]}})
            messages.append({"role": "user", "content": results})
            continue

        answer = "".join(b.get("text", "") for b in out_msg["content"])
        answer = re.sub(r"<thinking>.*?</thinking>", "", answer, flags=re.S)
        answer = re.sub(r"</?answer>", "", answer).strip()
        break

    latency_ms = (time.perf_counter() - t0) * 1000
    evidence = "\n".join(evidence_parts)
    if not answer:  # loop exhausted without a final answer
        answer = "Unable to conclude an investigation within the step budget."

    # ── output guardrail: hallucination firewall ──────────────────────────
    # Deterministic floor: if no tool returned substantive data, the answer can't
    # be grounded — withhold without spending a judge call.
    meaningful = [
        p for p in evidence_parts
        if "No data returned" not in p and len(p.strip()) > 80
    ]
    if not meaningful:
        score, withheld, display_text = 0.0, True, ESCALATION_MESSAGE
    else:
        score, withheld, display_text = evaluate_and_gate(answer, evidence, config)
    cost = _cost(model, total_in, total_out)

    # persist context (use the real answer so the conversation stays coherent)
    history.append({"role": "user", "content": [{"text": safe_message}]})
    history.append({"role": "assistant", "content": [{"text": answer or "(no answer)"}]})

    state.record_turn(domain, model, score, withheld)
    # Remember only grounded answers (never memorize a withheld hallucination).
    if not withheld and answer and "Unable to conclude" not in answer:
        sev_m = re.search(r"\bP[1-4]\b", answer)
        memory.remember(domain, safe_message, sev_m.group(0) if sev_m else "P?",
                        answer, model, score)
    LLMObs.annotate(
        output_data=display_text,
        tags={"domain": domain, "model": model, "withheld": str(withheld)},
        metrics={"tokens_in": total_in, "tokens_out": total_out, "grounding_score": score},
    )
    emit_turn_metrics(config, cost_usd=cost, latency_ms=latency_ms, model=model,
                      domain=domain, bedrock_calls=calls, withheld=withheld,
                      grounding_score=score, is_answer=True)

    return AgentResult(
        display_text=display_text,
        kind="answer",
        withheld=withheld,
        eval_score=round(score, 2),
        model=model,
        domain=domain,
        cost_usd=round(cost, 6),
        latency_ms=round(latency_ms),
        bedrock_calls=calls,
        tools_used=tools_used,
        routing_table=router.get_routing_table(),
        recalled=recalled_n,
    )
