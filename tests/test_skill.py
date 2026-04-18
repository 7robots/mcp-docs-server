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

    def test_documents_code_mode_meta_tools(self, body):
        # list_sources is the canonical "what backends exist" entry point
        # now that the static backend table has been removed from the skill.
        for tool in ("list_sources", "search", "get_schema", "execute", "call_tool"):
            assert tool in body, f"SKILL.md should reference meta-tool {tool!r}"

    def test_points_at_list_sources_not_static_table(self, body):
        """The skill should defer the backend catalog to `list_sources()`.

        A static markdown table of backend ids drifts out of sync with the
        live config, so it's been removed intentionally.
        """
        assert "list_sources" in body
        # Loose check: the body shouldn't contain the old two-column table.
        assert "| Backend id |" not in body

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
