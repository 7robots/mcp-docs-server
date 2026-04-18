"""Backend loader for mcp-docs-server.

Reads `backends.yaml`, resolves env-var references, optionally pulls
additional docs-tagged servers from a configured MCP marketplace
(`MCP_MARKETPLACE_URL`), and produces the `{"mcpServers": {...}}` dict
that `fastmcp.server.create_proxy` consumes.

Design:
- Missing env vars skip the affected backend (warning logged); server still starts.
- Disabled backends are silently ignored.
- Marketplace discovery is opt-in via `MCP_MARKETPLACE_URL`. On network
  or parse error the server boots with file-only backends.
- Marketplace results are cached for the process lifetime — restart to
  re-fetch, same as `backends.yaml`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

# Marketplace discovery is always filtered to the "docs" tag.
_MARKETPLACE_TAG = "docs"
_MARKETPLACE_TIMEOUT_SECONDS = 10.0

# Cached across the process lifetime. Cleared via `_reset_marketplace_cache()`
# in tests.
_MARKETPLACE_CACHE: list[dict[str, Any]] | None = None

LoadResult = dict[str, Any]


class BackendConfigError(ValueError):
    """Raised when backends.yaml is structurally invalid."""


def _resolve_env(value: str) -> str | None:
    """Expand a ${VAR} placeholder. Returns None if the variable is unset/empty."""
    if not (value.startswith("${") and value.endswith("}")):
        return value
    var_name = value[2:-1]
    resolved = os.environ.get(var_name, "")
    return resolved or None


def _build_headers(backend: dict[str, Any]) -> dict[str, str] | None:
    """Translate a backend's auth block into request headers.

    Returns None when the backend should be skipped due to missing credentials.
    """
    auth = backend.get("auth") or {"type": "none"}
    auth_type = auth.get("type", "none")

    if auth_type == "none":
        return {}

    if auth_type == "bearer":
        token_env = auth.get("token_env")
        if not token_env:
            raise BackendConfigError(
                f"backend {backend['id']!r}: auth.type=bearer requires token_env"
            )
        token = os.environ.get(token_env, "").strip()
        if not token:
            logger.warning(
                "Skipping backend %r: %s is not set in environment",
                backend["id"],
                token_env,
            )
            return None
        return {"Authorization": f"Bearer {token}"}

    raise BackendConfigError(
        f"backend {backend['id']!r}: unsupported auth.type {auth_type!r}"
    )


def load_backends_file(path: Path) -> list[dict[str, Any]]:
    """Load and lightly validate the raw backends list from YAML."""
    with path.open("r") as f:
        data = yaml.safe_load(f) or {}

    backends = data.get("backends")
    if not isinstance(backends, list):
        raise BackendConfigError(
            f"{path}: expected top-level `backends:` list"
        )

    seen_ids: set[str] = set()
    for entry in backends:
        if not isinstance(entry, dict):
            raise BackendConfigError(f"{path}: each backend entry must be a mapping")
        for required in ("id", "name", "url"):
            if required not in entry:
                raise BackendConfigError(
                    f"{path}: backend missing required field {required!r}: {entry!r}"
                )
        if entry["id"] in seen_ids:
            raise BackendConfigError(f"{path}: duplicate backend id {entry['id']!r}")
        seen_ids.add(entry["id"])

    return backends


def _marketplace_entry_to_backend(entry: dict[str, Any]) -> dict[str, Any]:
    """Translate a marketplace discovery response entry into our backend schema.

    Raises BackendConfigError when the entry can't be represented locally
    (e.g. OAuth servers — mcp-docs-server has no path to acquire those
    tokens at runtime).
    """
    for required in ("id", "name", "url"):
        if not entry.get(required):
            raise BackendConfigError(
                f"marketplace entry missing required field {required!r}: {entry!r}"
            )

    auth_type = entry.get("auth_type") or "none"
    if auth_type == "none":
        auth = {"type": "none"}
    elif auth_type in ("bearer", "api_key"):
        # Reuse the existing `{ID}_BEARER_TOKEN` convention so _build_headers
        # can skip the backend gracefully if the token env var is unset.
        token_env = entry["id"].upper().replace("-", "_") + "_BEARER_TOKEN"
        auth = {"type": "bearer", "token_env": token_env}
    else:
        raise BackendConfigError(
            f"marketplace entry {entry['id']!r}: unsupported auth_type {auth_type!r}"
        )

    return {
        "id": entry["id"],
        "name": entry["name"],
        "url": entry["url"],
        # Marketplace's `streamable-http` is FastMCP's `http` transport.
        "transport": "http",
        "auth": auth,
        "tags": list(entry.get("tags") or []),
        "enabled": True,
    }


def fetch_marketplace_backends() -> list[dict[str, Any]]:
    """Fetch docs-tagged backends from the configured MCP marketplace.

    Reads `MCP_MARKETPLACE_URL` (e.g. the discovery endpoint on an
    mcp-marketplace deployment). Returns an empty list when the env var
    is unset, the endpoint is unreachable, or the response is malformed —
    callers get file-only config in those cases.

    Result is cached for the process lifetime; call
    `_reset_marketplace_cache()` between tests.
    """
    global _MARKETPLACE_CACHE
    if _MARKETPLACE_CACHE is not None:
        return list(_MARKETPLACE_CACHE)

    base_url = os.environ.get("MCP_MARKETPLACE_URL", "").strip()
    if not base_url:
        _MARKETPLACE_CACHE = []
        return []

    try:
        with httpx.Client(timeout=httpx.Timeout(_MARKETPLACE_TIMEOUT_SECONDS)) as client:
            resp = client.get(base_url, params={"tag": _MARKETPLACE_TAG})
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as e:
        logger.warning(
            "Marketplace discovery failed (%s): continuing with file-only backends",
            e,
        )
        _MARKETPLACE_CACHE = []
        return []
    except ValueError as e:
        logger.warning(
            "Marketplace discovery response was not valid JSON (%s): continuing with file-only backends",
            e,
        )
        _MARKETPLACE_CACHE = []
        return []

    raw_servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(raw_servers, list):
        logger.warning(
            "Marketplace discovery response missing 'servers' list; continuing with file-only backends",
        )
        _MARKETPLACE_CACHE = []
        return []

    result: list[dict[str, Any]] = []
    for s in raw_servers:
        if not isinstance(s, dict):
            continue
        try:
            result.append(_marketplace_entry_to_backend(s))
        except BackendConfigError as e:
            logger.info("Skipping marketplace backend: %s", e)

    if result:
        logger.info(
            "Discovered %d marketplace backend(s): %s",
            len(result),
            ", ".join(b["id"] for b in result),
        )
    _MARKETPLACE_CACHE = result
    return list(result)


def _reset_marketplace_cache() -> None:
    """Clear the in-process marketplace cache. Test-only."""
    global _MARKETPLACE_CACHE
    _MARKETPLACE_CACHE = None


def _collect_all_backends(path: Path) -> list[dict[str, Any]]:
    """Load file backends, append marketplace-discovered ones (file wins on id conflict)."""
    file_backends = load_backends_file(path)
    existing_ids = {b["id"] for b in file_backends}
    combined = list(file_backends)

    for mb in fetch_marketplace_backends():
        if mb["id"] in existing_ids:
            logger.info(
                "Marketplace backend %r conflicts with backends.yaml; keeping the file version",
                mb["id"],
            )
            continue
        combined.append(mb)
        existing_ids.add(mb["id"])

    return combined


def build_proxy_config(path: Path) -> LoadResult:
    """Build the `{"mcpServers": {...}}` dict for `create_proxy`.

    Enabled backends with resolvable auth credentials are included. Others are
    dropped with a warning. If no backends survive, returns an empty config
    (the server still boots, exposing only the Code Mode discovery tools with
    no downstream catalog).
    """
    backends = _collect_all_backends(path)

    mcp_servers: dict[str, dict[str, Any]] = {}
    loaded: list[str] = []
    skipped: list[tuple[str, str]] = []

    for backend in backends:
        bid = backend["id"]
        if not backend.get("enabled", True):
            skipped.append((bid, "disabled in backends.yaml"))
            continue

        url = _resolve_env(backend["url"])
        if not url:
            skipped.append((bid, f"url resolves to empty: {backend['url']!r}"))
            continue

        headers = _build_headers(backend)
        if headers is None:
            skipped.append((bid, "missing auth credential"))
            continue

        entry: dict[str, Any] = {
            "url": url,
            "transport": backend.get("transport", "http"),
        }
        if headers:
            entry["headers"] = headers

        mcp_servers[bid] = entry
        loaded.append(bid)

    if loaded:
        logger.info("Loaded %d backend(s): %s", len(loaded), ", ".join(loaded))
    else:
        logger.warning("No backends loaded from %s", path)
    for bid, reason in skipped:
        logger.info("Skipped backend %r: %s", bid, reason)

    return {"mcpServers": mcp_servers}


def summarize_backends(path: Path) -> list[dict[str, Any]]:
    """Return a public-facing summary of backends for the `list_sources` tool.

    Mirrors `build_proxy_config`'s load/skip decisions but returns only the
    metadata safe to expose to an LLM (id, name, tags, loaded, skip_reason).
    URLs and auth details are intentionally omitted.

    Unlike `build_proxy_config`, this function does not log — it is called at
    tool-invocation time, not at server startup.
    """
    backends = _collect_all_backends(path)
    summaries: list[dict[str, Any]] = []

    for backend in backends:
        bid = backend["id"]
        summary: dict[str, Any] = {
            "id": bid,
            "name": backend.get("name", bid),
            "tags": list(backend.get("tags") or []),
            "loaded": False,
            "skip_reason": None,
        }

        if not backend.get("enabled", True):
            summary["skip_reason"] = "disabled"
            summaries.append(summary)
            continue

        if not _resolve_env(backend["url"]):
            summary["skip_reason"] = f"url unresolved: {backend['url']!r}"
            summaries.append(summary)
            continue

        try:
            headers = _build_headers(backend)
        except BackendConfigError as e:
            summary["skip_reason"] = f"invalid auth config: {e}"
            summaries.append(summary)
            continue

        if headers is None:
            auth = backend.get("auth") or {}
            summary["skip_reason"] = f"missing credential ({auth.get('token_env', '?')})"
            summaries.append(summary)
            continue

        summary["loaded"] = True
        summaries.append(summary)

    return summaries
