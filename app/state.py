"""Shared in-memory state for the agent.

Holds the rolling log of recent turns (used by the self-tune analyzer) and the
last recommendation produced (so an "apply" command can act on it). Kept in a
dedicated module so `agent` and `self_tune` can both use it without a circular
import.
"""

from __future__ import annotations

from typing import Any

# Rolling log of recent turns: {domain, model, score, withheld}
RECENT_TURNS: list[dict[str, Any]] = []

# Last self-tune recommendation: {domain, from_model, to_model, reason}
LAST_RECOMMENDATION: dict[str, Any] | None = None

_MAX_TURNS = 200


def record_turn(domain: str, model: str, score: float | None, withheld: bool) -> None:
    """Append a turn record, trimming to the most recent _MAX_TURNS."""
    RECENT_TURNS.append(
        {"domain": domain, "model": model, "score": score, "withheld": withheld}
    )
    if len(RECENT_TURNS) > _MAX_TURNS:
        del RECENT_TURNS[: len(RECENT_TURNS) - _MAX_TURNS]


def set_recommendation(rec: dict[str, Any] | None) -> None:
    global LAST_RECOMMENDATION
    LAST_RECOMMENDATION = rec
