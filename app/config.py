"""Application configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """Configuration for the SRE On-Call Agent.

    All values are loaded from environment variables with sensible defaults
    for the hackathon environment.
    """

    # AWS
    aws_region: str = "us-east-1"
    bedrock_model_default: str = "us.amazon.nova-lite-v1:0"
    bedrock_guardrail_id: str | None = None
    bedrock_guardrail_version: str = "DRAFT"

    # Datadog
    dd_api_key: str = ""
    dd_app_key: str = ""
    dd_site: str = "us5.datadoghq.com"
    dd_mcp_url: str = "https://mcp.us5.datadoghq.com/api/unstable/mcp-server/mcp"
    dd_llmobs_ml_app: str = "sre-oncall-agent"

    # Agent
    max_iterations: int = 10
    firewall_threshold: float = 0.5

    # Auto-triage (Point C): Datadog webhook -> agent -> Slack
    slack_webhook_url: str = ""   # Slack incoming webhook (optional)
    triage_token: str = ""        # shared secret required on POST /triage (optional)

    # Model routing (mutable at runtime)
    model_routing: dict = field(default_factory=lambda: {
        "db": "us.amazon.nova-lite-v1:0",
        "network": "us.amazon.nova-lite-v1:0",
        "app": "us.amazon.nova-lite-v1:0",
        "infra": "us.amazon.nova-lite-v1:0",
        "default": "us.amazon.nova-lite-v1:0",
    })

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            bedrock_model_default=os.getenv(
                "BEDROCK_MODEL_DEFAULT", "us.amazon.nova-lite-v1:0"
            ),
            bedrock_guardrail_id=os.getenv("BEDROCK_GUARDRAIL_ID"),
            bedrock_guardrail_version=os.getenv("BEDROCK_GUARDRAIL_VERSION", "DRAFT"),
            dd_api_key=os.getenv("DD_API_KEY", ""),
            dd_app_key=os.getenv("DD_APP_KEY", ""),
            dd_site=os.getenv("DD_SITE", "us5.datadoghq.com"),
            dd_mcp_url=os.getenv(
                "DD_MCP_URL",
                "https://mcp.us5.datadoghq.com/api/unstable/mcp-server/mcp",
            ),
            dd_llmobs_ml_app=os.getenv("DD_LLMOBS_ML_APP", "sre-oncall-agent"),
            max_iterations=int(os.getenv("MAX_ITERATIONS", "10")),
            firewall_threshold=float(os.getenv("FIREWALL_THRESHOLD", "0.5")),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
            triage_token=os.getenv("FT_TRIAGE_TOKEN", ""),
        )
