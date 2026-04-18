"""Tests for the backends.yaml loader (`mcp_docs.backends`)."""

from __future__ import annotations

import pytest
import yaml

from mcp_docs.backends import (
    BackendConfigError,
    build_proxy_config,
    load_backends_file,
)
from tests.conftest import backend_entry


def _yaml_doc(*entries) -> str:
    """Render a list of backend entries as a `backends:` YAML document."""
    return yaml.safe_dump({"backends": list(entries)}, sort_keys=False)


# ---------------------------------------------------------------------------
# load_backends_file — structural validation
# ---------------------------------------------------------------------------


class TestLoadBackendsFile:
    def test_loads_minimal_file(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        backends = load_backends_file(path)
        assert len(backends) == 1
        assert backends[0]["id"] == "a"

    def test_missing_top_level_key_raises(self, write_backends):
        path = write_backends("not_backends: []\n")
        with pytest.raises(BackendConfigError, match="backends"):
            load_backends_file(path)

    def test_backends_must_be_list(self, write_backends):
        path = write_backends("backends: oops\n")
        with pytest.raises(BackendConfigError):
            load_backends_file(path)

    def test_entry_must_be_mapping(self, write_backends):
        path = write_backends("backends:\n  - just-a-string\n")
        with pytest.raises(BackendConfigError, match="mapping"):
            load_backends_file(path)

    def test_duplicate_id_raises(self, write_backends):
        path = write_backends(_yaml_doc(
            backend_entry(id="x"),
            backend_entry(id="x"),
        ))
        with pytest.raises(BackendConfigError, match="duplicate"):
            load_backends_file(path)

    def test_missing_required_field_raises(self, write_backends):
        path = write_backends(
            "backends:\n"
            "  - id: incomplete\n"
            "    name: Incomplete\n"  # url missing
            "    auth: { type: none }\n"
        )
        with pytest.raises(BackendConfigError, match="url"):
            load_backends_file(path)


# ---------------------------------------------------------------------------
# build_proxy_config — auth header construction
# ---------------------------------------------------------------------------


class TestAuthHeaders:
    def test_none_auth_omits_headers(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="a", auth={"type": "none"})))
        cfg = build_proxy_config(path)
        assert "headers" not in cfg["mcpServers"]["a"]

    def test_bearer_auth_adds_authorization_header(self, write_backends, monkeypatch):
        monkeypatch.setenv("FOO_TOKEN", "sekret")
        path = write_backends(_yaml_doc(backend_entry(
            id="foo",
            auth={"type": "bearer", "token_env": "FOO_TOKEN"},
        )))
        cfg = build_proxy_config(path)
        assert cfg["mcpServers"]["foo"]["headers"] == {"Authorization": "Bearer sekret"}

    def test_bearer_missing_token_env_raises(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(
            id="foo",
            auth={"type": "bearer"},  # token_env missing
        )))
        with pytest.raises(BackendConfigError, match="token_env"):
            build_proxy_config(path)

    def test_bearer_unset_env_skips_backend(self, write_backends, monkeypatch):
        monkeypatch.delenv("ABSENT_TOKEN", raising=False)
        path = write_backends(_yaml_doc(backend_entry(
            id="works",
            auth={"type": "none"},
        ), backend_entry(
            id="broken",
            auth={"type": "bearer", "token_env": "ABSENT_TOKEN"},
        )))
        cfg = build_proxy_config(path)
        assert "broken" not in cfg["mcpServers"]
        assert "works" in cfg["mcpServers"]

    def test_bearer_blank_env_skips_backend(self, write_backends, monkeypatch):
        monkeypatch.setenv("EMPTY_TOKEN", "   ")  # whitespace-only
        path = write_backends(_yaml_doc(backend_entry(
            id="blank",
            auth={"type": "bearer", "token_env": "EMPTY_TOKEN"},
        )))
        cfg = build_proxy_config(path)
        assert cfg["mcpServers"] == {}

    def test_unsupported_auth_type_raises(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(
            id="weird",
            auth={"type": "magic"},
        )))
        with pytest.raises(BackendConfigError, match="auth.type"):
            build_proxy_config(path)


# ---------------------------------------------------------------------------
# build_proxy_config — enabled / disabled behavior
# ---------------------------------------------------------------------------


class TestEnabledFlag:
    def test_enabled_true_backend_loads(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="on", enabled=True)))
        cfg = build_proxy_config(path)
        assert "on" in cfg["mcpServers"]

    def test_enabled_false_backend_skipped(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="off", enabled=False)))
        cfg = build_proxy_config(path)
        assert cfg["mcpServers"] == {}

    def test_enabled_defaults_to_true(self, write_backends):
        """Backend without an explicit `enabled` field is treated as enabled."""
        path = write_backends(_yaml_doc(backend_entry(id="implicit", enabled=None)))
        cfg = build_proxy_config(path)
        assert "implicit" in cfg["mcpServers"]


# ---------------------------------------------------------------------------
# build_proxy_config — ${VAR} env interpolation
# ---------------------------------------------------------------------------


class TestEnvInterpolation:
    def test_url_env_resolved(self, write_backends, monkeypatch):
        monkeypatch.setenv("DYN_URL", "https://dynamic.example/mcp")
        path = write_backends(_yaml_doc(backend_entry(id="dyn", url="${DYN_URL}")))
        cfg = build_proxy_config(path)
        assert cfg["mcpServers"]["dyn"]["url"] == "https://dynamic.example/mcp"

    def test_literal_url_passed_through(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(
            id="lit",
            url="https://literal.example/mcp",
        )))
        cfg = build_proxy_config(path)
        assert cfg["mcpServers"]["lit"]["url"] == "https://literal.example/mcp"

    def test_unresolved_url_env_skips_backend(self, write_backends, monkeypatch):
        monkeypatch.delenv("ABSENT_URL", raising=False)
        path = write_backends(_yaml_doc(backend_entry(id="gone", url="${ABSENT_URL}")))
        cfg = build_proxy_config(path)
        assert cfg["mcpServers"] == {}


# ---------------------------------------------------------------------------
# build_proxy_config — output shape
# ---------------------------------------------------------------------------


class TestProxyConfigShape:
    def test_top_level_key_is_mcpServers(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        cfg = build_proxy_config(path)
        assert list(cfg.keys()) == ["mcpServers"]

    def test_transport_defaults_to_http(self, write_backends):
        path = write_backends(
            "backends:\n"
            "  - id: a\n"
            "    name: A\n"
            "    url: https://a.example/mcp\n"
            "    auth: { type: none }\n"
        )
        cfg = build_proxy_config(path)
        assert cfg["mcpServers"]["a"]["transport"] == "http"

    def test_transport_preserved(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="a", transport="sse")))
        cfg = build_proxy_config(path)
        assert cfg["mcpServers"]["a"]["transport"] == "sse"

    def test_empty_backends_yields_empty_config(self, write_backends):
        path = write_backends("backends: []\n")
        cfg = build_proxy_config(path)
        assert cfg == {"mcpServers": {}}

    def test_returned_dict_is_create_proxy_compatible(self, write_backends):
        """Shape must be what `fastmcp.server.create_proxy` accepts: `{mcpServers: {id: {url, transport, headers?}}}`."""
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        cfg = build_proxy_config(path)
        entry = cfg["mcpServers"]["a"]
        assert set(entry.keys()) <= {"url", "transport", "headers"}
        assert entry["url"].startswith("https://")


# ---------------------------------------------------------------------------
# Logging — load/skip summary is emitted
# ---------------------------------------------------------------------------


class TestLoadLogging:
    def test_loaded_backends_logged(self, write_backends, caplog):
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        with caplog.at_level("INFO", logger="mcp_docs.backends"):
            build_proxy_config(path)
        assert any("Loaded 1 backend(s)" in r.message for r in caplog.records)

    def test_skipped_backend_logged_with_reason(self, write_backends, caplog, monkeypatch):
        monkeypatch.delenv("ABSENT_TOKEN", raising=False)
        path = write_backends(_yaml_doc(backend_entry(
            id="broken",
            auth={"type": "bearer", "token_env": "ABSENT_TOKEN"},
        )))
        with caplog.at_level("WARNING", logger="mcp_docs.backends"):
            build_proxy_config(path)
        assert any("broken" in r.message and "ABSENT_TOKEN" in r.message for r in caplog.records)

    def test_no_backends_logged_at_warning(self, write_backends, caplog):
        path = write_backends("backends: []\n")
        with caplog.at_level("WARNING", logger="mcp_docs.backends"):
            build_proxy_config(path)
        assert any("No backends loaded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Shipped backends.yaml — the real config in the repo
# ---------------------------------------------------------------------------


class TestShippedBackendsYaml:
    def test_parses_cleanly(self, repo_root, clean_backend_env):
        cfg = build_proxy_config(repo_root / "backends.yaml")
        assert "mcpServers" in cfg

    def test_expected_backends_load(self, repo_root, clean_backend_env):
        cfg = build_proxy_config(repo_root / "backends.yaml")
        expected = {"fastmcp", "google", "cloudflare", "aws", "mslearn"}
        assert expected <= set(cfg["mcpServers"].keys())

    def test_all_urls_are_https(self, repo_root, clean_backend_env):
        cfg = build_proxy_config(repo_root / "backends.yaml")
        for bid, entry in cfg["mcpServers"].items():
            assert entry["url"].startswith("https://"), (
                f"backend {bid!r} must use https (got {entry['url']!r})"
            )

    def test_no_secrets_in_shipped_file(self, repo_root):
        """The committed backends.yaml should reference env vars, never literal tokens.

        Strips YAML comments first so explanatory text (e.g. "Bearer ${token_env}"
        in a `#` comment) doesn't trigger the check.
        """
        lines = (repo_root / "backends.yaml").read_text().splitlines()
        non_comment = "\n".join(
            line.split("#", 1)[0] for line in lines
        )
        for needle in ("Bearer ", "api_key:", "password:", "client_secret:"):
            assert needle not in non_comment, (
                f"suspicious literal {needle!r} in backends.yaml"
            )


# ---------------------------------------------------------------------------
# Marketplace discovery — fetching docs-tagged backends from an MCP marketplace
# ---------------------------------------------------------------------------


class TestMarketplaceDiscovery:
    """Dynamic backend discovery via `MCP_MARKETPLACE_URL`."""

    @staticmethod
    def _mock_transport(payload, status_code=200):
        """Build an httpx MockTransport that returns `payload` as JSON."""
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json=payload)

        return httpx.MockTransport(handler)

    def _patch_client(self, monkeypatch, transport):
        """Replace httpx.Client so every call goes through `transport`."""
        import httpx

        real_client = httpx.Client

        def fake_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", fake_client)

    def test_no_env_var_returns_empty(self, clean_marketplace_env):
        from mcp_docs.backends import fetch_marketplace_backends
        assert fetch_marketplace_backends() == []

    def test_empty_env_var_returns_empty(self, monkeypatch):
        monkeypatch.setenv("MCP_MARKETPLACE_URL", "")
        from mcp_docs.backends import fetch_marketplace_backends
        assert fetch_marketplace_backends() == []

    def test_fetch_translates_entries(self, monkeypatch):
        payload = {
            "servers": [
                {
                    "id": "fastmcp-docs",
                    "name": "FastMCP Docs",
                    "description": "FastMCP documentation",
                    "url": "https://gofastmcp.com/mcp",
                    "transport": "streamable-http",
                    "auth_type": "none",
                    "tags": ["docs", "fastmcp"],
                },
            ],
            "total": 1,
        }
        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        self._patch_client(monkeypatch, self._mock_transport(payload))

        from mcp_docs.backends import fetch_marketplace_backends
        result = fetch_marketplace_backends()
        assert len(result) == 1
        b = result[0]
        assert b["id"] == "fastmcp-docs"
        assert b["url"] == "https://gofastmcp.com/mcp"
        # streamable-http normalized to http
        assert b["transport"] == "http"
        assert b["auth"] == {"type": "none"}
        assert b["enabled"] is True
        assert "docs" in b["tags"]

    def test_bearer_auth_maps_to_token_env(self, monkeypatch):
        payload = {
            "servers": [
                {
                    "id": "secret-docs",
                    "name": "Secret Docs",
                    "description": "",
                    "url": "https://secret.example/mcp",
                    "transport": "streamable-http",
                    "auth_type": "bearer",
                    "tags": ["docs"],
                },
            ],
            "total": 1,
        }
        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        self._patch_client(monkeypatch, self._mock_transport(payload))

        from mcp_docs.backends import fetch_marketplace_backends
        result = fetch_marketplace_backends()
        assert result[0]["auth"] == {
            "type": "bearer",
            "token_env": "SECRET_DOCS_BEARER_TOKEN",
        }

    def test_oauth_entry_skipped(self, monkeypatch, caplog):
        payload = {
            "servers": [
                {
                    "id": "ok-docs",
                    "name": "OK",
                    "description": "",
                    "url": "https://ok.example/mcp",
                    "transport": "streamable-http",
                    "auth_type": "none",
                    "tags": ["docs"],
                },
                {
                    "id": "oauth-docs",
                    "name": "OAuth Only",
                    "description": "",
                    "url": "https://oauth.example/mcp",
                    "transport": "streamable-http",
                    "auth_type": "oauth",
                    "tags": ["docs"],
                },
            ],
            "total": 2,
        }
        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        self._patch_client(monkeypatch, self._mock_transport(payload))

        from mcp_docs.backends import fetch_marketplace_backends
        with caplog.at_level("INFO", logger="mcp_docs.backends"):
            result = fetch_marketplace_backends()
        ids = [b["id"] for b in result]
        assert ids == ["ok-docs"]
        assert any("unsupported auth_type" in r.message for r in caplog.records)

    def test_http_error_returns_empty(self, monkeypatch, caplog):
        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        self._patch_client(
            monkeypatch, self._mock_transport({"error": "nope"}, status_code=500)
        )

        from mcp_docs.backends import fetch_marketplace_backends
        with caplog.at_level("WARNING", logger="mcp_docs.backends"):
            assert fetch_marketplace_backends() == []
        assert any("Marketplace discovery failed" in r.message for r in caplog.records)

    def test_malformed_response_returns_empty(self, monkeypatch, caplog):
        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        self._patch_client(
            monkeypatch, self._mock_transport({"wrong_key": []}, status_code=200)
        )

        from mcp_docs.backends import fetch_marketplace_backends
        with caplog.at_level("WARNING", logger="mcp_docs.backends"):
            assert fetch_marketplace_backends() == []
        assert any("missing 'servers' list" in r.message for r in caplog.records)

    def test_cache_avoids_second_request(self, monkeypatch):
        payload = {
            "servers": [
                {
                    "id": "cached",
                    "name": "Cached",
                    "description": "",
                    "url": "https://cached.example/mcp",
                    "transport": "streamable-http",
                    "auth_type": "none",
                    "tags": ["docs"],
                },
            ],
            "total": 1,
        }
        call_count = {"n": 0}

        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(200, json=payload)

        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        self._patch_client(monkeypatch, httpx.MockTransport(handler))

        from mcp_docs.backends import fetch_marketplace_backends
        fetch_marketplace_backends()
        fetch_marketplace_backends()
        fetch_marketplace_backends()
        assert call_count["n"] == 1  # cached after first call

    def test_tag_filter_is_sent(self, monkeypatch):
        captured = {}

        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"servers": [], "total": 0})

        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        self._patch_client(monkeypatch, httpx.MockTransport(handler))

        from mcp_docs.backends import fetch_marketplace_backends
        fetch_marketplace_backends()
        assert "tag=docs" in captured["url"]


# ---------------------------------------------------------------------------
# build_proxy_config — integration with marketplace discovery
# ---------------------------------------------------------------------------


class TestBuildProxyWithMarketplace:
    def test_marketplace_backends_appended_to_file_backends(
        self, monkeypatch, write_backends, clean_backend_env
    ):
        import httpx

        payload = {
            "servers": [
                {
                    "id": "marketplace-docs",
                    "name": "Marketplace Docs",
                    "description": "",
                    "url": "https://mktpl.example/mcp",
                    "transport": "streamable-http",
                    "auth_type": "none",
                    "tags": ["docs"],
                },
            ],
            "total": 1,
        }

        def handler(request):
            return httpx.Response(200, json=payload)

        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        real_client = httpx.Client

        def fake_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", fake_client)

        path = write_backends(_yaml_doc(backend_entry(id="local-one")))
        cfg = build_proxy_config(path)
        assert "local-one" in cfg["mcpServers"]
        assert "marketplace-docs" in cfg["mcpServers"]

    def test_file_wins_on_id_conflict(
        self, monkeypatch, write_backends, clean_backend_env, caplog
    ):
        import httpx

        payload = {
            "servers": [
                {
                    "id": "dup",
                    "name": "Marketplace Dup",
                    "description": "",
                    "url": "https://marketplace.example/mcp",
                    "transport": "streamable-http",
                    "auth_type": "none",
                    "tags": ["docs"],
                },
            ],
            "total": 1,
        }

        def handler(request):
            return httpx.Response(200, json=payload)

        monkeypatch.setenv("MCP_MARKETPLACE_URL", "https://m.example/api/discovery/servers")
        real_client = httpx.Client

        def fake_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", fake_client)

        path = write_backends(
            _yaml_doc(backend_entry(id="dup", url="https://file.example/mcp"))
        )
        with caplog.at_level("INFO", logger="mcp_docs.backends"):
            cfg = build_proxy_config(path)
        assert cfg["mcpServers"]["dup"]["url"] == "https://file.example/mcp"
        assert any(
            "conflicts with backends.yaml" in r.message for r in caplog.records
        )
