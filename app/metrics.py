"""Custom metric emission to Datadog.

Design note: the spec calls for `datadog.statsd`, but statsd requires a local
Datadog Agent which we do not run (agentless hackathon environment). We instead
use the `datadog` library's HTTP API submission (`datadog.api.Metric.send`),
which is agentless and uses the same dependency. Metrics never raise - a
telemetry failure must not break a user's turn.
"""

from __future__ import annotations

import logging

import datadog

from app.config import Config

logger = logging.getLogger(__name__)

_initialized = False


def _short_model(model_id: str) -> str:
    """Turn 'us.amazon.nova-micro-v1:0' into a tag-safe 'nova-micro-v1-0'."""
    return model_id.split(".")[-1].replace(":", "-")


def _ensure_init(config: Config) -> None:
    global _initialized
    if not _initialized:
        datadog.initialize(
            api_key=config.dd_api_key,
            app_key=config.dd_app_key,
            api_host=f"https://api.{config.dd_site}",
        )
        _initialized = True


def emit_turn_metrics(
    config: Config,
    *,
    cost_usd: float,
    latency_ms: float,
    model: str,
    domain: str,
    bedrock_calls: int = 1,
    withheld: bool = False,
    guardrail_blocked: bool = False,
    grounding_score: float | None = None,
    is_answer: bool = True,
) -> None:
    """Emit per-turn metrics. Tagged with model + domain + ml_app.

    is_answer=False for non-investigation turns (e.g. guardrail-blocked input),
    which are excluded from the quality-SLO counters.
    """
    try:
        _ensure_init(config)
        tags = [
            f"model:{_short_model(model)}",
            f"domain:{domain}",
            f"ml_app:{config.dd_llmobs_ml_app}",
        ]
        series = [
            {"metric": "sre_agent.bedrock.calls", "points": bedrock_calls, "type": "count", "tags": tags},
            {"metric": "sre_agent.bedrock.cost_usd", "points": cost_usd, "type": "gauge", "tags": tags},
            {"metric": "sre_agent.bedrock.latency_ms", "points": latency_ms, "type": "gauge", "tags": tags},
        ]
        if grounding_score is not None:
            series.append({"metric": "sre_agent.grounding_score", "points": grounding_score, "type": "gauge", "tags": tags})
        if is_answer:
            series.append({"metric": "sre_agent.answer.total", "points": 1, "type": "count", "tags": tags})
            if withheld:
                series.append({"metric": "sre_agent.hallucination", "points": 1, "type": "count", "tags": tags})
            else:
                series.append({"metric": "sre_agent.answer.grounded", "points": 1, "type": "count", "tags": tags})
        if guardrail_blocked:
            series.append({"metric": "sre_agent.guardrail_blocks", "points": 1, "type": "count", "tags": tags})
        datadog.api.Metric.send(series)
        logger.info("Emitted %d metric series", len(series))
    except Exception as exc:  # never break the turn on telemetry failure
        logger.warning("Metric emission failed: %s", exc)
