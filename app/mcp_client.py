"""Datadog MCP JSON-RPC 2.0 client.

Communicates with the Datadog MCP Server over HTTPS using the JSON-RPC 2.0
protocol. Manages session state via the Mcp-Session-Id header and filters
available tools to read-only operations only.
"""

import logging
from typing import Any

import requests

from app.config import Config

logger = logging.getLogger(__name__)

# Tools the agent is allowed to use (read-only operations only)
ALLOWED_TOOLS = frozenset({
    "search_datadog_logs",
    "search_datadog_metrics",
    "search_datadog_monitors",
})

# Request timeout in seconds
REQUEST_TIMEOUT = 30


class MCPClientError(Exception):
    """Raised when the MCP client encounters an error."""


class DatadogMCPClient:
    """JSON-RPC 2.0 client for the Datadog MCP Server.

    Handles session management, tool discovery, and tool invocation
    against the Datadog MCP endpoint. Only exposes read-only tools
    to prevent unintended mutations.

    Usage:
        config = Config.from_env()
        client = DatadogMCPClient(config)
        client.initialize()
        tools = client.list_tools()
        result = client.call_tool("search_datadog_logs", {"query": "service:web"})
    """

    def __init__(self, config: Config) -> None:
        """Initialize the MCP client with application config.

        Args:
            config: Application configuration containing DD API keys and MCP URL.
        """
        self._config = config
        self._id: int = 0
        self._session_id: str | None = None
        self._base_headers: dict[str, str] = {
            "Content-Type": "application/json",
            "DD-API-KEY": config.dd_api_key,
            "DD-APPLICATION-KEY": config.dd_app_key,
            "Accept": "application/json, text/event-stream",
        }

    def _post(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request to the MCP server.

        Args:
            method: The JSON-RPC method name (e.g. 'initialize', 'tools/list').
            params: Optional parameters for the method call.

        Returns:
            The parsed JSON response from the server.

        Raises:
            MCPClientError: On network failures, timeouts, or non-200 responses.
        """
        self._id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or {},
        }

        headers = {**self._base_headers}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            response = requests.post(
                self._config.dd_mcp_url,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout as exc:
            raise MCPClientError(
                f"Request to MCP server timed out after {REQUEST_TIMEOUT}s"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise MCPClientError(
                f"Failed to connect to MCP server at {self._config.dd_mcp_url}: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise MCPClientError(f"MCP request failed: {exc}") from exc

        if response.status_code != 200:
            raise MCPClientError(
                f"MCP server returned HTTP {response.status_code}: {response.text[:500]}"
            )

        # Persist session ID for subsequent requests
        if "Mcp-Session-Id" in response.headers:
            self._session_id = response.headers["Mcp-Session-Id"]

        return response.json()

    def initialize(self) -> None:
        """Initialize the MCP session with protocol version and client info.

        Must be called before list_tools() or call_tool(). Establishes
        the session with the MCP server and negotiates capabilities.

        Raises:
            MCPClientError: If initialization fails.
        """
        logger.info("Initializing MCP session with %s", self._config.dd_mcp_url)
        self._post("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "sre-oncall-agent", "version": "1.0"},
        })
        logger.info("MCP session initialized (session_id=%s)", self._session_id)

    def list_tools(self) -> list[dict[str, Any]]:
        """List available tools, filtered to read-only operations.

        Returns only the tools in the ALLOWED_TOOLS set to prevent
        the agent from performing write operations.

        Returns:
            List of tool descriptors with name, description, and inputSchema.

        Raises:
            MCPClientError: If the request fails.
        """
        result = self._post("tools/list")
        all_tools: list[dict[str, Any]] = result.get("result", {}).get("tools", [])

        # Filter to only read-only tools
        filtered = [t for t in all_tools if t.get("name") in ALLOWED_TOOLS]
        logger.info(
            "Listed %d tools from MCP server, %d allowed after filtering",
            len(all_tools),
            len(filtered),
        )
        return filtered

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool on the MCP server.

        Args:
            name: The tool name to invoke (must be in ALLOWED_TOOLS).
            arguments: The tool's input arguments.

        Returns:
            The text content from the tool's response.

        Raises:
            MCPClientError: If the tool is not allowed or the request fails.
        """
        if name not in ALLOWED_TOOLS:
            raise MCPClientError(
                f"Tool '{name}' is not in the allowed read-only tool set: {sorted(ALLOWED_TOOLS)}"
            )

        logger.info("Calling MCP tool: %s", name)
        result = self._post("tools/call", {"name": name, "arguments": arguments})

        # Extract text from content blocks
        content: list[dict[str, Any]] = result.get("result", {}).get("content", [])
        if content:
            return content[0].get("text", "")
        return ""
