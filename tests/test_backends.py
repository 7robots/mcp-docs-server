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
