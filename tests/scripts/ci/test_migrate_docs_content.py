"""Tests for documentation content migration script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
MIGRATE_SCRIPT = ROOT / "scripts" / "ci" / "site" / "migrate-docs-content.py"


def _load_migrate_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "migrate_docs_content",
        MIGRATE_SCRIPT,
    )
    assert_that(spec).is_not_none()
    assert spec is not None  # narrow type for mypy
    assert_that(spec.loader).is_not_none()
    assert spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _point_at(
    migrate: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    docs_src = tmp_path / "docs"
    docs_src.mkdir(exist_ok=True)
    site_content = tmp_path / "apps" / "site" / "src" / "content" / "docs"
    route_map = tmp_path / "apps" / "site" / "src" / "generated" / "docs-route-map.ts"
    monkeypatch.setattr(migrate, "ROOT", tmp_path)
    monkeypatch.setattr(migrate, "DOCS_SRC", docs_src)
    monkeypatch.setattr(migrate, "DOCS_DEST", site_content)
    monkeypatch.setattr(migrate, "ROUTE_MAP_DEST", route_map)
    return docs_src, site_content


@pytest.fixture
def isolated_docs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Path]:
    """Point migration paths at a temporary docs tree."""
    migrate = _load_migrate_module()
    docs_src, site_content = _point_at(migrate, monkeypatch, tmp_path)
    (docs_src / "getting-started.md").write_text(
        "# Getting Started\n\nInstall lintro.\n",
        encoding="utf-8",
    )
    return migrate, site_content


def test_main_writes_frontmatter(isolated_docs: tuple[Any, Path]) -> None:
    """Migration should emit Astro frontmatter for markdown sources."""
    migrate, site_content = isolated_docs
    migrate.main()
    output = site_content / "start" / "getting-started.md"
    assert_that(output.exists()).is_true()
    text = output.read_text(encoding="utf-8")
    assert_that(text).starts_with("---\n")
    assert_that(text).contains('title: "Getting Started"')
    assert_that(text).contains('navTitle: "Getting started"')
    assert_that(text).contains("category: start")
    assert_that(text).contains("navGroup: start")
    assert_that(text).contains("Install lintro.")


def test_main_strips_leading_h1(isolated_docs: tuple[Any, Path]) -> None:
    """The layout renders the title, so the body must not repeat the H1."""
    migrate, site_content = isolated_docs
    migrate.main()
    body = (site_content / "start" / "getting-started.md").read_text(encoding="utf-8")
    assert_that(body.split("---")[2]).does_not_contain("# Getting Started")


def test_main_writes_route_map(isolated_docs: tuple[Any, Path]) -> None:
    """Migration should emit a source→doc route map for the site link layer."""
    migrate, site_content = isolated_docs
    migrate.main()
    route_map = (site_content.parents[1] / "generated" / "docs-route-map.ts").read_text(
        encoding="utf-8",
    )
    assert_that(route_map).contains("export const sourceToDoc")
    assert_that(route_map).contains('"getting-started.md": "start/getting-started",')


def test_main_generates_ai_landing(isolated_docs: tuple[Any, Path]) -> None:
    """Sections without a README get a generated landing page."""
    migrate, site_content = isolated_docs
    migrate.main()
    landing = (site_content / "ai" / "index.md").read_text(encoding="utf-8")
    assert_that(landing).contains('title: "AI"')
    assert_that(landing).contains("category: ai")
    assert_that(landing).contains("order: 5")


def test_main_rejects_unmapped_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A docs/ file without a mapping must fail the migration loudly."""
    migrate = _load_migrate_module()
    docs_src, _ = _point_at(migrate, monkeypatch, tmp_path)
    (docs_src / "brand-new-guide.md").write_text("# New\n\nBody.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="brand-new-guide.md"):
        migrate.main()


def test_skipped_sources_do_not_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ADR template is deliberately unpublished."""
    migrate = _load_migrate_module()
    docs_src, site_content = _point_at(migrate, monkeypatch, tmp_path)
    (docs_src / "adr").mkdir()
    (docs_src / "adr" / "template.md").write_text(
        "# ADR-NNNN: Title\n",
        encoding="utf-8",
    )
    migrate.main()
    assert_that((site_content / "project" / "adr" / "template.md").exists()).is_false()


def test_rewrite_root_readme_links_targets_github() -> None:
    """Links escaping docs/ to the repo README should point at GitHub."""
    migrate = _load_migrate_module()
    hub_body = (
        "See [main README](../README.md) and [install](../README.md#installation)."
    )
    rewritten = migrate.rewrite_root_readme_links(hub_body, "")
    assert_that(rewritten).contains("(https://github.com/lgtm-hq/py-lintro)")
    assert_that(rewritten).contains(
        "(https://github.com/lgtm-hq/py-lintro#installation)",
    )
    assert_that(rewritten).does_not_contain("README.md")


def test_rewrite_repo_file_links_target_github_blob_and_tree() -> None:
    """Files and directories outside docs/ have no site page; link to GitHub."""
    migrate = _load_migrate_module()
    body = (
        "See [ci](../.github/workflows/test-ci.yml) and "
        "[samples](../test_samples/) and [nested](../../outside.md)."
    )

    rewritten = migrate.rewrite_root_readme_links(body, "")

    assert_that(rewritten).contains(
        "(https://github.com/lgtm-hq/py-lintro/blob/main/.github/workflows/test-ci.yml)",
    )
    assert_that(rewritten).contains(
        "(https://github.com/lgtm-hq/py-lintro/tree/main/test_samples)",
    )
    assert_that(rewritten).contains("(../../outside.md)")


def test_rewrite_root_readme_links_keeps_docs_internal_links() -> None:
    """A ../README.md link from a nested dir targets the docs hub, not GitHub."""
    migrate = _load_migrate_module()
    nested_body = "Back to the [docs hub](../README.md)."
    assert_that(
        migrate.rewrite_root_readme_links(nested_body, "architecture"),
    ).is_equal_to(
        nested_body,
    )
    escaping_body = "See the [main README](../../README.md#quick-start)."
    assert_that(
        migrate.rewrite_root_readme_links(escaping_body, "architecture"),
    ).contains(
        "(https://github.com/lgtm-hq/py-lintro#quick-start)",
    )


def test_every_table_source_exists_in_repo() -> None:
    """A DOC_SPECS row pointing at a deleted file must fail, not vanish."""
    migrate = _load_migrate_module()
    missing = [
        spec.source
        for spec in migrate.DOC_SPECS
        if not (migrate.DOCS_SRC / spec.source).exists()
    ]

    assert_that(missing).is_empty()


def test_docs_paths_use_repo_layout() -> None:
    """Default paths should target py-lintro docs and site content."""
    migrate = _load_migrate_module()
    assert_that(migrate.DOCS_SRC.name).is_equal_to("docs")
    assert_that(migrate.DOCS_DEST.parts[-3:]).is_equal_to(("src", "content", "docs"))


def test_description_skips_code_fences_and_callouts() -> None:
    """Meta descriptions come from prose, never from fenced code or quotes."""
    migrate = _load_migrate_module()
    body = "# Title\n\n> **TL;DR** skip me\n\n```yaml\nkey: value\n```\n\nReal prose here.\n"
    assert_that(migrate.description_from_markdown(body)).is_equal_to("Real prose here.")


def test_tool_migration_uses_short_titles_and_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool, ADR and guide pages carry short nav labels and navGroup frontmatter."""
    migrate = _load_migrate_module()
    docs_root, site_content = _point_at(migrate, monkeypatch, tmp_path)
    tools_src = docs_root / "tool-analysis"
    tools_src.mkdir(parents=True)
    (docs_root / "adr").mkdir()
    (docs_root / "configuration.md").write_text(
        "# Configuration Guide\n\nBody.\n",
        encoding="utf-8",
    )
    (docs_root / "watch-mode.md").write_text(
        "# Watch Mode\n\nBody.\n",
        encoding="utf-8",
    )
    (docs_root / "mcp.md").write_text("# MCP Server\n\nBody.\n", encoding="utf-8")
    (tools_src / "README.md").write_text(
        "# Tool Analysis Documentation\n\nHub.\n",
        encoding="utf-8",
    )
    (tools_src / "ruff-analysis.md").write_text(
        "# Ruff Tool Analysis\n\nBody.\n",
        encoding="utf-8",
    )
    (docs_root / "adr" / "0001-native-parser-per-tool.md").write_text(
        "# ADR-0001: A dedicated native parser per tool\n\nBody.\n",
        encoding="utf-8",
    )

    migrate.main()

    index = (site_content / "tools" / "index.md").read_text(encoding="utf-8")
    ruff = (site_content / "tools" / "ruff.md").read_text(encoding="utf-8")
    config = (site_content / "guides" / "configuration.md").read_text(encoding="utf-8")
    watch = (site_content / "guides" / "watch-mode.md").read_text(encoding="utf-8")
    mcp = (site_content / "ai" / "mcp.md").read_text(encoding="utf-8")
    adr = (
        site_content / "project" / "adr" / "0001-native-parser-per-tool.md"
    ).read_text(
        encoding="utf-8",
    )
    assert_that(index).contains('title: "Tool Analysis Documentation"')
    assert_that(index).contains('navTitle: "Tools"')
    assert_that(ruff).contains('title: "Ruff Tool Analysis"')
    assert_that(ruff).contains('navTitle: "ruff"')
    assert_that(ruff).contains("navGroup: python")
    assert_that(config).contains('navTitle: "Configuration"')
    assert_that(config).contains("navGroup: setup")
    assert_that(watch).contains('navTitle: "Watch mode"')
    assert_that(watch).contains("order: 20")
    assert_that(mcp).contains("category: ai")
    assert_that(mcp).contains("navGroup: agents")
    assert_that(adr).contains('navTitle: "ADR-0001 A dedicated native parser per tool"')
    assert_that(adr).contains("navGroup: decisions")


def test_unknown_tool_page_requires_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool page missing from TOOL_GROUPS must not land in an 'other' group."""
    migrate = _load_migrate_module()
    docs_root, _ = _point_at(migrate, monkeypatch, tmp_path)
    tools_src = docs_root / "tool-analysis"
    tools_src.mkdir(parents=True)
    (tools_src / "newtool-analysis.md").write_text("# New\n\nBody.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="TOOL_GROUPS"):
        migrate.main()
