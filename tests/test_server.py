"""Tests for server.py wiring: CodeMode transform, SkillProvider, proxy, auth."""

from __future__ import annotations

import importlib

import pytest


def _fresh_server(monkeypatch, **env: str):
    """Re-import `server` with a controlled environment.

    `server.py` reads env at import time (e.g. `_create_auth` decides whether
    to build MultiAuth). We clear cached modules so each test gets a clean
    import under its own env.
    """
    import sys

    for var in (
        "OKTA_CLIENT_ID",
        "OKTA_CLIENT_SECRET",
        "OKTA_DOMAIN",
        "OKTA_ISSUER",
        "MCP_BASE_URL",
        "JWT_SIGNING_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    sys.modules.pop("server", None)
    return importlib.import_module("server")


# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------


class TestServerImports:
    def test_server_module_imports(self, clean_okta_env, clean_backend_env):
        server = _fresh_server(pytest.MonkeyPatch())
        assert server.mcp is not None

    def test_server_name(self, clean_okta_env, clean_backend_env, monkeypatch):
        server = _fresh_server(monkeypatch)
        assert server.mcp.name == "mcp-docs-server"

    def test_instructions_reference_code_mode(self, clean_okta_env, clean_backend_env, monkeypatch):
        server = _fresh_server(monkeypatch)
        assert server.mcp.instructions is not None
        # Instructions should teach the client about the Code Mode meta-tools
        # so an LLM picking the server up cold knows what to do.
        text = server.mcp.instructions.lower()
        assert "search" in text
        assert "get_schema" in text
        assert "call_tool" in text


# ---------------------------------------------------------------------------
# CodeMode transform
# ---------------------------------------------------------------------------


class TestCodeMode:
    def test_code_mode_transform_registered(self, clean_okta_env, clean_backend_env, monkeypatch):
        from fastmcp.experimental.transforms.code_mode import CodeMode

        server = _fresh_server(monkeypatch)
        matching = [t for t in server.mcp.transforms if isinstance(t, CodeMode)]
        assert len(matching) == 1, "expected exactly one CodeMode transform"

    def test_no_duplicate_transforms(self, clean_okta_env, clean_backend_env, monkeypatch):
        server = _fresh_server(monkeypatch)
        names = [type(t).__name__ for t in server.mcp.transforms]
        assert len(names) == len(set(names)), f"duplicate transforms: {names}"


# ---------------------------------------------------------------------------
# Proxy provider — backends wired in
# ---------------------------------------------------------------------------


class TestProxyProvider:
    def test_proxy_provider_registered(self, clean_okta_env, clean_backend_env, monkeypatch):
        from fastmcp.server.providers.proxy import ProxyProvider

        server = _fresh_server(monkeypatch)
        assert any(isinstance(p, ProxyProvider) for p in server.mcp.providers)

    def test_server_is_fastmcp_proxy(self, clean_okta_env, clean_backend_env, monkeypatch):
        """create_proxy() returns FastMCPProxy — a FastMCP subclass."""
        from fastmcp.server.providers.proxy import FastMCPProxy

        server = _fresh_server(monkeypatch)
        assert isinstance(server.mcp, FastMCPProxy)


# ---------------------------------------------------------------------------
# Skill provider — SKILL.md exposed as a resource
# ---------------------------------------------------------------------------


class TestSkillProvider:
    def test_skill_provider_registered(self, clean_okta_env, clean_backend_env, monkeypatch):
        from fastmcp.server.providers.skills import SkillProvider

        server = _fresh_server(monkeypatch)
        providers = [p for p in server.mcp.providers if isinstance(p, SkillProvider)]
        assert len(providers) == 1, "expected exactly one SkillProvider"

    def test_skill_provider_points_at_docs_router(self, clean_okta_env, clean_backend_env, monkeypatch):
        from fastmcp.server.providers.skills import SkillProvider

        server = _fresh_server(monkeypatch)
        providers = [p for p in server.mcp.providers if isinstance(p, SkillProvider)]
        # SkillProvider stores the resolved path on _skill_path
        assert providers[0]._skill_path.name == "docs-router"


# ---------------------------------------------------------------------------
# Auth wiring — opt-in via OKTA_CLIENT_SECRET
# ---------------------------------------------------------------------------


class TestAuthWiring:
    def test_auth_disabled_without_okta_secret(self, clean_okta_env, clean_backend_env, monkeypatch):
        server = _fresh_server(monkeypatch)
        assert server.mcp.auth is None

    def test_create_auth_returns_none_when_unconfigured(self, clean_okta_env, monkeypatch):
        server = _fresh_server(monkeypatch)
        assert server._create_auth() is None
