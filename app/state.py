"""Shared agent state — persisted to disk so it survives a process/systemd
restart (only an instance terminate, which wipes the disk, loses it). No Redis:
plain files on the local filesystem.

Persists `state.json` under FT_DATA_DIR (default: sre-agent/data/): recent turns,
last recommendation, and routing overrides. The compounding markdown knowledge
base (ops-memory.md) is owned by `app/memory.py`.
"""

from __future__ import annotations

import json
import os
from typing import Any

_DATA_DIR = os.environ.get(
    "FT_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
)
_STATE_FILE = os.path.join(_DATA_DIR, "state.json")
_MAX_TURNS = 200

# ── live state (mirrors state.json) ────────────────────────────────────────
RECENT_TURNS: list[dict[str, Any]] = []
LAST_RECOMMENDATION: dict[str, Any] | None = None
ROUTING_OVERRIDES: dict[str, str] = {}  # domain -> model_id (from applied self-tunes)


def _save() -> None:
    """Atomically persist machine state to disk."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(
                {
                    "recent_turns": RECENT_TURNS[-_MAX_TURNS:],
                    "last_recommendation": LAST_RECOMMENDATION,
                    "routing_overrides": ROUTING_OVERRIDES,
                },
                f,
                indent=2,
            )
        os.replace(tmp, _STATE_FILE)  # atomic
    except Exception:
        pass  # never let persistence break a turn


def _load() -> None:
    global LAST_RECOMMENDATION
    try:
        with open(_STATE_FILE) as f:
            data = json.load(f)
        RECENT_TURNS[:] = data.get("recent_turns", [])
        LAST_RECOMMENDATION = data.get("last_recommendation")
        ROUTING_OVERRIDES.clear()
        ROUTING_OVERRIDES.update(data.get("routing_overrides", {}))
    except FileNotFoundError:
        pass
    except Exception:
        pass


def record_turn(domain: str, model: str, score: float | None, withheld: bool) -> None:
    RECENT_TURNS.append(
        {"domain": domain, "model": model, "score": score, "withheld": withheld}
    )
    if len(RECENT_TURNS) > _MAX_TURNS:
        del RECENT_TURNS[: len(RECENT_TURNS) - _MAX_TURNS]
    _save()


def set_recommendation(rec: dict[str, Any] | None) -> None:
    global LAST_RECOMMENDATION
    LAST_RECOMMENDATION = rec
    _save()


def set_routing_override(domain: str, model_id: str) -> None:
    ROUTING_OVERRIDES[domain] = model_id
    _save()


_load()  # hydrate from disk on import
