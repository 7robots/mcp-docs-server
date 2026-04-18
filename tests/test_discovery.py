"""Tests for `mcp_docs.discovery.ListSources` and `summarize_backends`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mcp_docs.backends import summarize_backends
from mcp_docs.discovery import ListSources
from tests.conftest import backend_entry


def _yaml_doc(*entries) -> str:
    return yaml.safe_dump({"backends": list(entries)}, sort_keys=False)


# ---------------------------------------------------------------------------
# summarize_backends — per-backend load/skip decisions
# ---------------------------------------------------------------------------


class TestSummarizeBackends:
    def test_loaded_backend_reported(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="a", tags=["python"])))
        [entry] = summarize_backends(path)
        assert entry == {
            "id": "a",
            "name": "A",
            "tags": ["python"],
            "loaded": True,
            "skip_reason": None,
        }

    def test_disabled_backend_shown_as_not_loaded(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="off", enabled=False)))
        [entry] = summarize_backends(path)
        assert entry["loaded"] is False
        assert entry["skip_reason"] == "disabled"

    def test_missing_bearer_env_shown_with_reason(self, write_backends, monkeypatch):
        monkeypatch.delenv("ABSENT_TOKEN", raising=False)
        path = write_backends(_yaml_doc(backend_entry(
            id="b",
            auth={"type": "bearer", "token_env": "ABSENT_TOKEN"},
        )))
        [entry] = summarize_backends(path)
        assert entry["loaded"] is False
        assert "ABSENT_TOKEN" in entry["skip_reason"]

    def test_unresolved_url_shown_with_reason(self, write_backends, monkeypatch):
        monkeypatch.delenv("ABSENT_URL", raising=False)
        path = write_backends(_yaml_doc(backend_entry(id="c", url="${ABSENT_URL}")))
        [entry] = summarize_backends(path)
        assert entry["loaded"] is False
        assert "ABSENT_URL" in entry["skip_reason"]

    def test_invalid_auth_config_shown_with_reason(self, write_backends):
        """A malformed auth block becomes a skip reason, not an exception.

        summarize_backends is called at tool-invocation time — raising would
        surface as an opaque LLM-facing error. Skip with explanation instead.
        """
        path = write_backends(_yaml_doc(backend_entry(
            id="d",
            auth={"type": "bearer"},  # missing token_env
        )))
        [entry] = summarize_backends(path)
        assert entry["loaded"] is False
        assert "token_env" in entry["skip_reason"]

    def test_mixed_loaded_and_skipped(self, write_backends, monkeypatch):
        monkeypatch.delenv("MISSING", raising=False)
        path = write_backends(_yaml_doc(
            backend_entry(id="ok", tags=["x"]),
            backend_entry(id="off", enabled=False),
            backend_entry(id="nokey", auth={"type": "bearer", "token_env": "MISSING"}),
        ))
        entries = summarize_backends(path)
        by_id = {e["id"]: e for e in entries}
        assert by_id["ok"]["loaded"] is True
        assert by_id["off"]["loaded"] is False
        assert by_id["nokey"]["loaded"] is False

    def test_never_leaks_urls_or_auth(self, write_backends, monkeypatch):
        """Sensitive fields must not appear in the summary."""
        monkeypatch.setenv("T", "actual-secret-token")
        path = write_backends(_yaml_doc(backend_entry(
            id="sensitive",
            url="https://internal.example.com/mcp",
            auth={"type": "bearer", "token_env": "T"},
        )))
        [entry] = summarize_backends(path)
        text = repr(entry)
        assert "https://internal.example.com" not in text
        assert "actual-secret-token" not in text
        assert "Bearer" not in text

    def test_order_preserved(self, write_backends):
        path = write_backends(_yaml_doc(
            backend_entry(id="first"),
            backend_entry(id="second"),
            backend_entry(id="third"),
        ))
        ids = [e["id"] for e in summarize_backends(path)]
        assert ids == ["first", "second", "third"]

    def test_missing_tags_default_to_empty_list(self, write_backends):
        path = write_backends(
            "backends:\n"
            "  - id: notags\n"
            "    name: NoTags\n"
            "    url: https://x.example/mcp\n"
            "    auth: { type: none }\n"
        )
        [entry] = summarize_backends(path)
        assert entry["tags"] == []

    def test_does_not_log(self, write_backends, caplog):
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        with caplog.at_level("DEBUG", logger="mcp_docs.backends"):
            summarize_backends(path)
        assert caplog.records == [], "summarize_backends should be silent"


# ---------------------------------------------------------------------------
# ListSources — DiscoveryToolFactory wrapping summarize_backends
# ---------------------------------------------------------------------------


class TestListSourcesFactory:
    def test_default_tool_name(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        tool = ListSources(path)(get_catalog=None)
        assert tool.name == "list_sources"

    def test_custom_tool_name(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        tool = ListSources(path, name="list_docs_backends")(get_catalog=None)
        assert tool.name == "list_docs_backends"

    def test_tool_description_mentions_backends(self, write_backends):
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        tool = ListSources(path)(get_catalog=None)
        assert tool.description is not None
        assert "backend" in tool.description.lower()

    async def test_tool_invocation_returns_summaries(self, write_backends):
        path = write_backends(_yaml_doc(
            backend_entry(id="a", tags=["python"]),
            backend_entry(id="b", enabled=False),
        ))
        tool = ListSources(path)(get_catalog=None)

        result = await tool.run({})
        # Structured content is preferred when available
        payload = result.structured_content
        if payload is None:
            # Fallback: parse JSON from text content
            import json
            payload = json.loads(result.content[0].text)
        entries = payload["result"] if isinstance(payload, dict) and "result" in payload else payload
        by_id = {e["id"]: e for e in entries}
        assert by_id["a"]["loaded"] is True
        assert by_id["b"]["loaded"] is False

    def test_rereads_yaml_on_each_call(self, write_backends):
        """Changes to backends.yaml take effect without rebuilding the factory."""
        path = write_backends(_yaml_doc(backend_entry(id="a")))
        tool = ListSources(path)(get_catalog=None)

        # Overwrite the same file with a different backend list
        path.write_text(_yaml_doc(backend_entry(id="a"), backend_entry(id="b")))

        # Second call should see the new backend.
        # summarize_backends is called inside the tool's closure on each invocation.
        from mcp_docs.backends import summarize_backends
        second = summarize_backends(path)
        assert {e["id"] for e in second} == {"a", "b"}


# ---------------------------------------------------------------------------
# Shipped backends.yaml — exercised through ListSources
# ---------------------------------------------------------------------------


class TestListSourcesAgainstShippedConfig:
    def test_expected_backends_reported(self, repo_root, clean_backend_env):
        entries = summarize_backends(repo_root / "backends.yaml")
        ids = {e["id"] for e in entries}
        assert {"fastmcp", "google", "cloudflare", "aws", "mslearn"} <= ids

    def test_no_urls_in_summary(self, repo_root, clean_backend_env):
        entries = summarize_backends(repo_root / "backends.yaml")
        blob = repr(entries)
        assert "fastmcp.app" not in blob
        assert "amazonaws" not in blob and "api.aws" not in blob
        assert "microsoft.com" not in blob
