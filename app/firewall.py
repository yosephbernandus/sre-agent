"""Hallucination firewall: LLM-judge grounding evaluation + response gating.

A separate (cheap) Bedrock call scores how well the agent's answer is grounded
in the evidence it actually retrieved. The score is submitted to Datadog LLM
Observability as an evaluation and attached to the active span. If the score is
below the configured threshold, the answer is withheld and the user is told to
escalate - this is the output guardrail.
"""

from __future__ import annotations

import logging
import re

import boto3
from ddtrace.llmobs import LLMObs

from app.config import Config

logger = logging.getLogger(__name__)

_JUDGE_MODEL = "us.amazon.nova-lite-v1:0"  # cheap but a more reliable scorer than micro

_JUDGE_SYSTEM = [
    {
        "text": (
            "You are a grounding evaluator for an SRE assistant. You are given "
            "EVIDENCE (logs, metrics, or monitors retrieved from Datadog) and the "
            "assistant's ANSWER. Score 0.0-1.0 how well the answer's root-cause claim "
            "is supported by the EVIDENCE, using this rubric:\n"
            "- 0.8-1.0: the root cause is clearly backed by specific evidence "
            "(e.g. a matching error log, or an alerting monitor naming the same service).\n"
            "- 0.4-0.7: evidence is related/partial but not conclusive.\n"
            "- 0.0-0.3: the answer asserts a specific cause that the evidence does NOT "
            "support, or no relevant evidence was retrieved (hallucination).\n"
            "If a relevant error log or alerting monitor is present and the answer matches it, "
            "score at least 0.7. Reply with ONLY the number, nothing else."
        )
    }
]

_NUM_RE = re.compile(r"(\d*\.?\d+)")

ESCALATION_MESSAGE = (
    "Answer withheld - the assistant could not ground this response in retrieved "
    "evidence (low confidence). Escalating to a human on-call engineer rather than "
    "risk a hallucinated root cause."
)


def judge_grounding(answer: str, evidence: str, config: Config) -> float:
    """Score (0..1) how grounded `answer` is in `evidence` via Nova Micro."""
    if not answer.strip():
        return 0.0
    client = boto3.client("bedrock-runtime", region_name=config.aws_region)
    prompt = (
        f"EVIDENCE:\n{evidence or '(no evidence was retrieved)'}\n\n"
        f"ANSWER:\n{answer}\n\n"
        "Grounding score (0.0-1.0):"
    )
    try:
        resp = client.converse(
            modelId=_JUDGE_MODEL,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            system=_JUDGE_SYSTEM,
            inferenceConfig={"maxTokens": 10, "temperature": 0.0},
        )
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        match = _NUM_RE.search(text)
        score = float(match.group(1)) if match else 0.0
        return max(0.0, min(1.0, score))
    except Exception as exc:
        logger.warning("Judge call failed (%s); defaulting score to 0.5", exc)
        return 0.5  # neutral on judge failure - don't block on infra error


def _submit_evaluation(score: float) -> None:
    """Attach the grounding score to the active span as a Datadog evaluation."""
    try:
        span_ctx = LLMObs.export_span()
        LLMObs.submit_evaluation_for(
            span=span_ctx,
            label="grounding_score",
            metric_type="score",
            value=score,
        )
    except Exception as exc:
        logger.debug("submit_evaluation_for unavailable (%s); annotating instead", exc)
    try:
        LLMObs.annotate(metrics={"grounding_score": score})
    except Exception:
        pass


def evaluate_and_gate(
    answer: str, evidence: str, config: Config
) -> tuple[float, bool, str]:
    """Judge the answer, record the eval, and gate it.

    Returns (score, withheld, display_text).
    """
    score = judge_grounding(answer, evidence, config)
    _submit_evaluation(score)
    withheld = score < config.firewall_threshold
    display_text = ESCALATION_MESSAGE if withheld else answer
    logger.info("Firewall: score=%.2f threshold=%.2f withheld=%s",
                score, config.firewall_threshold, withheld)
    return score, withheld, display_text
