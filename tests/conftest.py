"""Shared test fixtures and helpers for mcp-docs-server.

Provides:
- `repo_root`       — absolute path to the repo root
- `clean_okta_env`  — clears all Okta env vars so auth returns None
- `clean_backend_env` — clears per-backend bearer tokens between tests
- `write_backends`  — factory that writes a backends.yaml from a string
- `backend_yaml`    — factory that builds a backends-yaml snippet from entries
"""

from __future__ import annotations

import os
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

OKTA_ENV_VARS = (
    "OKTA_CLIENT_ID",
    "OKTA_CLIENT_SECRET",
    "OKTA_DOMAIN",
    "OKTA_ISSUER",
    "MCP_BASE_URL",
    "JWT_SIGNING_KEY",
)

BACKEND_BEARER_ENV_VARS = (
    "AWS_KNOWLEDGE_BEARER_TOKEN",
    "GITHUB_DOCS_BEARER_TOKEN",
)


# ---------------------------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repo root."""
    return REPO_ROOT


# ---------------------------------------------------------------------------
# Environment fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_okta_env(monkeypatch) -> None:
    """Remove all Okta env vars so `_create_auth()` returns None."""
    for var in OKTA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def clean_backend_env(monkeypatch) -> None:
    """Remove per-backend bearer tokens so `backends.yaml` loads the none-auth entries only."""
    for var in BACKEND_BEARER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_marketplace_cache() -> None:
    """Clear the module-level marketplace-discovery cache between tests.

    Prevents one test's mocked response from leaking into another test's
    expectations. Runs autouse so every test gets a clean slate.
    """
    from mcp_docs.backends import _reset_marketplace_cache as _reset
    _reset()
    yield
    _reset()


@pytest.fixture
def clean_marketplace_env(monkeypatch) -> None:
    """Remove MCP_MARKETPLACE_URL so marketplace discovery is disabled by default."""
    monkeypatch.delenv("MCP_MARKETPLACE_URL", raising=False)


# ---------------------------------------------------------------------------
# backends.yaml helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def write_backends(tmp_path) -> Callable[[str], Path]:
    """Return a helper that writes a backends.yaml file and returns its path."""

    def _write(yaml_text: str) -> Path:
        path = tmp_path / "backends.yaml"
        path.write_text(textwrap.dedent(yaml_text).lstrip())
        return path

    return _write


def backend_entry(
    *,
    id: str = "test",
    name: str | None = None,
    url: str = "https://test.example/mcp",
    transport: str = "http",
    auth: dict[str, Any] | None = None,
    enabled: bool | None = True,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build a single backend entry as a dict — mirrors marketplace's `_server(...)` helper."""
    entry: dict[str, Any] = {
        "id": id,
        "name": name or id.title(),
        "url": url,
        "transport": transport,
        "auth": auth if auth is not None else {"type": "none"},
    }
    if enabled is not None:
        entry["enabled"] = enabled
    if tags is not None:
        entry["tags"] = tags
    return entry
