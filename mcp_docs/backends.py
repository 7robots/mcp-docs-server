"""Backend loader for mcp-docs-server.

Reads `backends.yaml`, resolves env-var references, and produces the
`{"mcpServers": {...}}` dict that `fastmcp.server.create_proxy` consumes.

Design:
- Missing env vars skip the affected backend (warning logged); server still starts.
- Disabled backends are silently ignored.
- No network calls here — pure config transform.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

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


def build_proxy_config(path: Path) -> LoadResult:
    """Build the `{"mcpServers": {...}}` dict for `create_proxy`.

    Enabled backends with resolvable auth credentials are included. Others are
    dropped with a warning. If no backends survive, returns an empty config
    (the server still boots, exposing only the Code Mode discovery tools with
    no downstream catalog).
    """
    backends = load_backends_file(path)

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
