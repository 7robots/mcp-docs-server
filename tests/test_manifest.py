"""Tests for the fastmcp.json deployment manifest and its consistency with the repo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "fastmcp.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


class TestManifestStructure:
    def test_manifest_exists(self):
        assert MANIFEST_PATH.exists()

    def test_has_schema_reference(self, manifest):
        assert manifest.get("$schema", "").startswith("https://gofastmcp.com/")

    def test_source_points_at_server_py(self, manifest):
        assert manifest["source"]["path"] == "server.py"
        assert manifest["source"]["entrypoint"] == "mcp"

    def test_python_requirement_matches_pyproject(self, manifest):
        """Manifest's required python must match pyproject.toml."""
        import tomllib

        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        assert manifest["environment"]["python"] == pyproject["project"]["requires-python"]

    def test_deployment_uses_http_transport(self, manifest):
        assert manifest["deployment"]["transport"] == "http"

    def test_deployment_binds_all_interfaces(self, manifest):
        """fastmcp.cloud expects the server to bind to 0.0.0.0."""
        assert manifest["deployment"]["host"] == "0.0.0.0"


# ---------------------------------------------------------------------------
# Env-var passthrough — declared vars match what code references
# ---------------------------------------------------------------------------


class TestManifestEnv:
    REQUIRED_OKTA_KEYS = {
        "OKTA_CLIENT_ID",
        "OKTA_CLIENT_SECRET",
        "OKTA_DOMAIN",
        "OKTA_ISSUER",
        "MCP_BASE_URL",
        "JWT_SIGNING_KEY",
    }

    def test_declares_all_okta_env_vars(self, manifest):
        declared = set(manifest["deployment"]["env"].keys())
        missing = self.REQUIRED_OKTA_KEYS - declared
        assert not missing, f"fastmcp.json missing Okta env vars: {missing}"

    def test_declares_bearer_token_vars_for_backend_auth(self, manifest):
        """Any bearer-auth backend in backends.yaml must have its token_env declared here."""
        backends = yaml.safe_load((REPO_ROOT / "backends.yaml").read_text())
        needed_env: set[str] = set()
        for entry in backends.get("backends", []):
            auth = entry.get("auth") or {}
            if auth.get("type") == "bearer":
                token_env = auth.get("token_env")
                if token_env:
                    needed_env.add(token_env)

        declared = set(manifest["deployment"]["env"].keys())
        missing = needed_env - declared
        assert not missing, (
            f"backends.yaml references bearer env vars not declared in "
            f"fastmcp.json: {missing}"
        )

    def test_all_env_values_are_placeholders(self, manifest):
        """Every env value should be `${VAR}` — no literals baked into the manifest."""
        for key, value in manifest["deployment"]["env"].items():
            assert value == f"${{{key}}}", (
                f"fastmcp.json env[{key}] should be '${{{key}}}', got {value!r}"
            )
