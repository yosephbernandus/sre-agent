"""Domain-based model router.

Maps an incident domain (db / network / app / infra) to a Bedrock model ID.
The routing table is mutable at runtime so the self-tune workflow can apply a
recommendation (e.g. route DB incidents to a stronger model).
"""

from __future__ import annotations

import logging

from app.config import Config

logger = logging.getLogger(__name__)


class ModelRouter:
    """Selects a Bedrock model based on the classified incident domain."""

    def __init__(self, config: Config) -> None:
        # copy so runtime mutations don't leak back into the Config default
        self._routing: dict[str, str] = dict(config.model_routing)
        self._default = config.bedrock_model_default

    def get_model(self, domain: str) -> str:
        """Return the model ID for a domain, falling back to the default."""
        return self._routing.get(domain, self._routing.get("default", self._default))

    def apply_recommendation(self, domain: str, model_id: str) -> None:
        """Update the routing for a domain (used by self-tune apply)."""
        prev = self._routing.get(domain)
        self._routing[domain] = model_id
        logger.info("Routing updated: %s %s -> %s", domain, prev, model_id)

    def get_routing_table(self) -> dict[str, str]:
        """Return a copy of the current routing state."""
        return dict(self._routing)
