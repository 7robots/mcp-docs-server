"""Tests for the inlined Okta `_create_auth()` function in `server.py`."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


def _import_server():
    """Import (or re-import) server.py fresh. Clears any cached module state."""
    sys.modules.pop("server", None)
    return importlib.import_module("server")


# ---------------------------------------------------------------------------
# Auth disabled path — unconfigured env
# ---------------------------------------------------------------------------


class TestAuthDisabled:
    def test_returns_none_when_secret_missing(self, clean_okta_env):
        server = _import_server()
        assert server._create_auth() is None

    def test_returns_none_when_secret_empty(self, clean_okta_env, monkeypatch):
        monkeypatch.setenv("OKTA_CLIENT_SECRET", "")
        server = _import_server()
        assert server._create_auth() is None


# ---------------------------------------------------------------------------
# Auth enabled path — env fully populated
# ---------------------------------------------------------------------------


class TestAuthEnabled:
    """Exercise the MultiAuth construction without hitting Okta.

    `server.py` calls `_create_auth()` at import time, and the real OIDCProxy
    constructor dials Okta's discovery endpoint. So we:

    1. Import `server.py` with Okta env UNSET (auth is None at import time).
    2. Set full Okta env inside each test.
    3. Invoke `server._create_auth()` under patched constructors.
    """

    @pytest.fixture
    def server_module(self, clean_okta_env):
        """Server module imported cleanly (auth disabled at import time)."""
        return _import_server()

    @pytest.fixture
    def full_okta_env(self, monkeypatch):
        monkeypatch.setenv("OKTA_CLIENT_SECRET", "s3cret")
        monkeypatch.setenv("OKTA_CLIENT_ID", "client-id")
        monkeypatch.setenv("OKTA_DOMAIN", "https://example.okta.com")
        monkeypatch.setenv("MCP_BASE_URL", "https://srv.fastmcp.app/mcp")

    @pytest.fixture
    def patched_auth(self):
        """Yield mocks for the three auth constructors as a tuple."""
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy") as oidc, \
             patch("fastmcp.server.auth.providers.introspection.IntrospectionTokenVerifier") as introspect, \
             patch("fastmcp.server.auth.MultiAuth") as multi_auth:
            oidc.return_value = MagicMock(name="OIDCProxy")
            introspect.return_value = MagicMock(name="IntrospectionTokenVerifier")
            multi_auth.return_value = MagicMock(name="MultiAuth")
            yield oidc, introspect, multi_auth

    def test_builds_multi_auth(self, server_module, full_okta_env, patched_auth):
        oidc, introspect, multi_auth = patched_auth

        result = server_module._create_auth()

        assert result is multi_auth.return_value
        multi_auth.assert_called_once()
        kwargs = multi_auth.call_args.kwargs
        assert kwargs["server"] is oidc.return_value
        assert introspect.return_value in kwargs["verifiers"]

    def test_oidc_proxy_configured_from_env(self, server_module, full_okta_env, patched_auth):
        oidc, _, _ = patched_auth
        server_module._create_auth()

        kwargs = oidc.call_args.kwargs
        assert kwargs["client_id"] == "client-id"
        assert kwargs["client_secret"] == "s3cret"
        assert kwargs["base_url"] == "https://srv.fastmcp.app/mcp"
        assert kwargs["config_url"].endswith("/.well-known/openid-configuration")

    def test_issuer_defaults_to_default_authz_server(self, server_module, full_okta_env, patched_auth):
        oidc, _, _ = patched_auth
        server_module._create_auth()

        assert oidc.call_args.kwargs["config_url"] == (
            "https://example.okta.com/oauth2/default/.well-known/openid-configuration"
        )

    def test_explicit_issuer_overrides_default(self, server_module, full_okta_env, patched_auth, monkeypatch):
        monkeypatch.setenv("OKTA_ISSUER", "https://example.okta.com/oauth2/custom")
        oidc, _, _ = patched_auth
        server_module._create_auth()

        assert oidc.call_args.kwargs["config_url"].startswith(
            "https://example.okta.com/oauth2/custom/"
        )

    def test_introspection_cache_ttl_set(self, server_module, full_okta_env, patched_auth):
        _, introspect, _ = patched_auth
        server_module._create_auth()

        assert introspect.call_args.kwargs["cache_ttl_seconds"] == 300

    def test_claude_ai_redirect_uri_allowed(self, server_module, full_okta_env, patched_auth):
        oidc, _, _ = patched_auth
        server_module._create_auth()

        allowed = oidc.call_args.kwargs["allowed_client_redirect_uris"]
        assert any("claude.ai" in uri for uri in allowed)
        assert any("localhost" in uri for uri in allowed)
