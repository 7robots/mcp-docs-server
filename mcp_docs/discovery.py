"""CodeMode discovery tool: `list_sources()`.

Surfaces the live set of documentation backends wired into this gateway so an
LLM can pick the right source without reading SKILL.md first. Added to
`CodeMode(discovery_tools=[...])` so it sits alongside `search` / `get_schema`
rather than being hidden behind `search`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp.experimental.transforms.code_mode import GetToolCatalog
from fastmcp.tools.tool import Tool

from mcp_docs.backends import summarize_backends


class ListSources:
    """`DiscoveryToolFactory` that exposes the backend registry as a tool.

    Each call reads `backends.yaml` fresh so newly-enabled backends appear
    immediately after a redeploy. URLs and auth details are never returned —
    only `id`, `name`, `tags`, `loaded`, and `skip_reason`.

    Example:
        ```python
        CodeMode(discovery_tools=[
            Search(),
            GetSchemas(),
            ListSources(backends_path=HERE / "backends.yaml"),
        ])
        ```
    """

    def __init__(
        self,
        backends_path: Path,
        *,
        name: str = "list_sources",
    ) -> None:
        self._backends_path = backends_path
        self._name = name

    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        path = self._backends_path

        async def list_sources() -> list[dict[str, Any]]:
            """List documentation sources available in this gateway.

            Returns one entry per backend with:
              - `id`          — prefix used on backend tool names (e.g. `cloudflare`)
              - `name`        — human-readable label
              - `tags`        — topic keywords; feed into `search(query, tags=[...])`
                               to scope a search to specific sources
              - `loaded`      — true if the backend is currently reachable and
                               its tools are proxied; false if it was skipped
              - `skip_reason` — present when `loaded` is false (e.g. "disabled",
                               "missing credential (X_TOKEN)")

            Call this once when you need to know which doc sources are available
            or to debug a "search returned nothing" case.
            """
            return summarize_backends(path)

        return Tool.from_function(fn=list_sources, name=self._name)
