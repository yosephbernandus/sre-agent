"""Input guardrails: PII + prompt-injection.

Two layers:
1. Primary — Amazon Bedrock Guardrails via `guardrailConfig` on `converse()`
   (when a guardrail ID is configured). Bedrock filters PII / prompt attacks.
2. Fallback — regex PII detection + prompt-injection keyword check, used when no
   guardrail ID is set (so the safety story holds even without a provisioned
   Bedrock guardrail).

Capability guard (read-only MCP tools) is enforced in `mcp_client.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import Config

# Fallback PII patterns
_PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
}

# Prompt-injection / jailbreak markers
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "you are now",
    "system prompt",
    "reveal your instructions",
    "developer mode",
)


@dataclass
class GuardrailResult:
    blocked: bool
    reason: str = ""
    sanitized: str = ""


def build_guardrail_config(config: Config) -> dict | None:
    """Return a Bedrock `guardrailConfig` dict, or None if not configured."""
    if not config.bedrock_guardrail_id:
        return None
    return {
        "guardrailIdentifier": config.bedrock_guardrail_id,
        "guardrailVersion": config.bedrock_guardrail_version,
    }


def check_input(text: str, config: Config) -> GuardrailResult:
    """Fallback input check (used when no Bedrock guardrail ID is set).

    Redacts PII and blocks obvious prompt-injection attempts. When a Bedrock
    guardrail IS configured, this is a no-op (Bedrock handles it inline).
    """
    if config.bedrock_guardrail_id:
        return GuardrailResult(blocked=False, sanitized=text)

    lowered = text.lower()
    for marker in _INJECTION_MARKERS:
        if marker in lowered:
            return GuardrailResult(
                blocked=True,
                reason=(
                    "Request blocked by guardrail: possible prompt-injection "
                    "attempt detected. Rephrase your incident question."
                ),
            )

    sanitized = text
    redacted = []
    for label, pattern in _PII_PATTERNS.items():
        if pattern.search(sanitized):
            sanitized = pattern.sub(f"[REDACTED_{label.upper()}]", sanitized)
            redacted.append(label)

    if redacted:
        # Not a hard block — redact and continue, but flag it.
        return GuardrailResult(
            blocked=False,
            reason=f"redacted: {', '.join(redacted)}",
            sanitized=sanitized,
        )
    return GuardrailResult(blocked=False, sanitized=sanitized)
