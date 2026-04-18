"""Tests for the bundled `skills/docs-router/SKILL.md` resource."""

from __future__ import annotations

from pathlib import Path

import pytest


SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "docs-router" / "SKILL.md"


# ---------------------------------------------------------------------------
# File presence and frontmatter
# ---------------------------------------------------------------------------


class TestSkillFile:
    def test_skill_md_exists(self):
        assert SKILL_PATH.exists(), f"SKILL.md missing at {SKILL_PATH}"

    def test_skill_md_non_empty(self):
        assert SKILL_PATH.stat().st_size > 0

    def test_has_yaml_frontmatter(self):
        text = SKILL_PATH.read_text()
        assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
        assert text.count("---\n") >= 2, "SKILL.md needs closing `---` on the frontmatter block"

    def test_frontmatter_has_name_and_description(self):
        import yaml

        text = SKILL_PATH.read_text()
        parts = text.split("---\n", 2)
        assert len(parts) == 3, "malformed frontmatter"
        meta = yaml.safe_load(parts[1]) or {}
        assert meta.get("name") == "docs-router"
        assert isinstance(meta.get("description"), str)
        assert len(meta["description"]) > 20, "description should be substantive"


# ---------------------------------------------------------------------------
# Content expectations — client-neutral, teaches Code Mode
# ---------------------------------------------------------------------------


class TestSkillContent:
    @pytest.fixture
    def body(self) -> str:
        text = SKILL_PATH.read_text()
        # Strip frontmatter
        return text.split("---\n", 2)[-1]

    def test_lists_current_backends(self, body):
        for bid in ("fastmcp", "google", "cloudflare", "aws", "mslearn"):
            assert bid in body, f"SKILL.md should mention backend id {bid!r}"

    def test_documents_code_mode_meta_tools(self, body):
        for tool in ("search", "get_schema", "execute", "call_tool"):
            assert tool in body, f"SKILL.md should reference meta-tool {tool!r}"

    def test_includes_example_code(self, body):
        assert "```python" in body, "SKILL.md should include a Python example block"

    def test_is_client_neutral(self, body):
        """Client-neutral by request: no brand-specific prompting conventions."""
        # Informational references to Claude are fine; prompting syntax isn't.
        forbidden = ("<thinking>", "[INST]", "<|im_start|>", "{%", "<<SYS>>")
        for marker in forbidden:
            assert marker not in body, (
                f"SKILL.md contains client-specific prompting marker {marker!r}"
            )
