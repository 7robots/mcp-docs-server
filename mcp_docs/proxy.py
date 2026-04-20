"""MCPConfigTransport subclass that disables Authorization-header forwarding.

FastMCP's `ProxyClient.__init__` (and `_create_client_factory`) unconditionally
set `forward_incoming_headers = True` on each backend's HTTP transport so the
caller's `Authorization:` header is relayed downstream. For mcp-docs-server
this is actively harmful: the caller authenticates with an Okta bearer token
scoped to *our* server, and relaying it to public docs backends (AWS
Knowledge, Cloudflare Docs) causes them to respond `401 + WWW-Authenticate`
pointing at their own OAuth discovery. That 401 propagates back to Claude
Desktop, which then launches a second OAuth dance per backend — exactly the
UX bug this subclass fixes.

We override `_create_proxy` to flip the flag back to `False` after the base
class connects each backend. The flag is read per-request (see
`StreamableHttpTransport._build_request_headers`), so late mutation is safe.
"""

from __future__ import annotations

import contextlib
import datetime
from typing import Any

from fastmcp.client.transports.base import ClientTransport
from fastmcp.client.transports.config import MCPConfigTransport
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.mcp_config import MCPServerTypes
from fastmcp.server import FastMCP


class NoForwardMCPConfigTransport(MCPConfigTransport):
    """`MCPConfigTransport` that suppresses `forward_incoming_headers` on backends."""

    def __init__(self, config: dict[str, Any] | Any, name_as_prefix: bool = True):
        super().__init__(config=config, name_as_prefix=name_as_prefix)
        # The single-backend branch in the base class pre-creates `self.transport`
        # eagerly. It inherits the default `forward_incoming_headers = False`
        # from StreamableHttpTransport/SSETransport, but override anyway to be
        # explicit and future-proof against default changes.
        if len(self.config.mcpServers) == 1 and isinstance(
            self.transport, StreamableHttpTransport | SSETransport
        ):
            self.transport.forward_incoming_headers = False

    async def _create_proxy(
        self,
        name: str,
        config: MCPServerTypes,
        timeout: datetime.timedelta | None,
        stack: contextlib.AsyncExitStack,
    ) -> tuple[ClientTransport, Any, FastMCP[Any]]:
        transport, client, proxy = await super()._create_proxy(
            name, config, timeout, stack
        )
        if isinstance(transport, StreamableHttpTransport | SSETransport):
            transport.forward_incoming_headers = False
        return transport, client, proxy
