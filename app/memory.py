"""Ops Memory Wiki — a compounding markdown knowledge base (Karpathy "LLM-Wiki"
pattern) that the agent maintains and reads back. No vector DB: plain markdown +
keyword/domain matching (RAG-lite).

- remember(): append a structured incident entry — only for grounded answers, so
  we never memorize hallucinations.
- recall(): before a new investigation, find past incidents in the same domain
  whose symptom overlaps the new query, and return them as context so the agent
  learns from history (and can generate runbooks from real precedent).

File: FT_DATA_DIR/ops-memory.md (gitignored runtime state, survives restart).
"""

from __future__ import annotations

import os
import re

_DATA_DIR = os.environ.get(
    "FT_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
)
_WIKI = os.path.join(_DATA_DIR, "ops-memory.md")

_STOP = {
    "the", "a", "an", "on", "in", "of", "is", "are", "to", "for", "and", "or", "what",
    "why", "how", "investigate", "incident", "issue", "problem", "error", "severity",
    "root", "cause", "give", "check", "service",
}
_HEADER = "# CodeCraft Ops Memory\n\nCompounding knowledge base of past investigations (LLM-Wiki). Newest at bottom.\n"


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9\-]+", text.lower()) if len(w) > 2 and w not in _STOP}


def remember(domain: str, symptom: str, severity: str, answer: str,
             model: str, score: float, ts: str = "") -> None:
    """Append one structured incident entry to the wiki (grounded answers only)."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        new = not os.path.exists(_WIKI)
        # collapse the answer to a compact root-cause/next-step blob
        body = " ".join(answer.split())[:600]
        with open(_WIKI, "a") as f:
            if new:
                f.write(_HEADER)
            f.write(
                f"\n## [{domain}] {symptom[:80]}\n"
                f"- when: {ts or 'recent'}\n"
                f"- severity: {severity}\n"
                f"- model: {model.split('.')[-1]} · grounding: {score:.2f}\n"
                f"- finding: {body}\n"
            )
    except Exception:
        pass


def _entries() -> list[dict]:
    """Parse the wiki into entries: {domain, symptom, text}."""
    try:
        with open(_WIKI) as f:
            raw = f.read()
    except FileNotFoundError:
        return []
    out = []
    for block in raw.split("\n## ")[1:]:
        head = block.partition("\n")[0]
        m = re.match(r"\[([^\]]+)\]\s*(.*)", head.strip())
        domain = m.group(1) if m else ""
        symptom = m.group(2) if m else head.strip()
        out.append({"domain": domain, "symptom": symptom, "text": "## " + block.strip()})
    return out


def recall(domain: str, query: str, k: int = 3) -> str:
    """Return up to k past incidents (same domain) most similar to the query.

    Empty string if nothing relevant. Pure keyword overlap — no model, no vector DB.
    """
    q = _tokens(query)
    scored = []
    for e in _entries():
        if e["domain"] and e["domain"] != domain:
            continue
        overlap = len(q & _tokens(e["symptom"] + " " + e["text"]))
        if overlap > 0:
            scored.append((overlap, e["text"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [t for _, t in scored[:k]]
    return "\n\n".join(top)


def read_all() -> str:
    """Return the full wiki markdown (for the /memory endpoint)."""
    try:
        with open(_WIKI) as f:
            return f.read()
    except FileNotFoundError:
        return "# CodeCraft Ops Memory\n\n(empty — no investigations recorded yet)\n"
