"""Tests for documentation content migration script."""

from __future__ import annotations

import importlib.util
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
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_docs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Path]:
    """Point migration paths at a temporary docs tree."""
    migrate = _load_migrate_module()
    docs_src = tmp_path / "docs"
    docs_src.mkdir()
    (docs_src / "getting-started.md").write_text(
        "# Getting Started\n\nInstall lintro.\n",
        encoding="utf-8",
    )
    site_content = tmp_path / "apps" / "site" / "src" / "content" / "docs"
    route_map = tmp_path / "apps" / "site" / "src" / "generated" / "docs-route-map.ts"
    monkeypatch.setattr(migrate, "ROOT", tmp_path)
    monkeypatch.setattr(migrate, "DOCS_SRC", docs_src)
    monkeypatch.setattr(migrate, "DOCS_DEST", site_content)
    monkeypatch.setattr(migrate, "ROUTE_MAP_DEST", route_map)
    return migrate, site_content


def test_main_writes_frontmatter(isolated_docs: tuple[Any, Path]) -> None:
    """Migration should emit Astro frontmatter for markdown sources."""
    migrate, site_content = isolated_docs
    migrate.main()
    output = site_content / "getting-started" / "getting-started.md"
    assert_that(output.exists()).is_true()
    text = output.read_text(encoding="utf-8")
    assert_that(text).starts_with("---\n")
    assert_that(text).contains("category: getting-started")
    assert_that(text).contains("# Getting Started")


def test_main_writes_route_map(isolated_docs: tuple[Any, Path]) -> None:
    """Migration should emit a source→doc route map for the site link layer."""
    migrate, site_content = isolated_docs
    migrate.main()
    route_map = (site_content.parents[1] / "generated" / "docs-route-map.ts").read_text(
        encoding="utf-8",
    )
    assert_that(route_map).contains("export const sourceToDoc")
    assert_that(route_map).contains(
        '"getting-started.md": "getting-started/getting-started",',
    )


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


def test_docs_paths_use_repo_layout() -> None:
    """Default paths should target py-lintro docs and site content."""
    migrate = _load_migrate_module()
    assert_that(migrate.DOCS_SRC.name).is_equal_to("docs")
    assert_that(migrate.DOCS_DEST.parts[-3:]).is_equal_to(("src", "content", "docs"))


def test_tool_migration_uses_short_titles_and_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool and usage pages should use short titles and navGroup frontmatter."""
    migrate = _load_migrate_module()
    docs_root = tmp_path / "docs"
    docs_src = docs_root / "tool-analysis"
    docs_src.mkdir(parents=True)
    (docs_root / "configuration.md").write_text(
        "# Configuration Guide\n\nBody.\n",
        encoding="utf-8",
    )
    (docs_src / "README.md").write_text(
        "# Tool Analysis Documentation\n\nHub.\n",
        encoding="utf-8",
    )
    (docs_src / "ruff-analysis.md").write_text(
        "# Ruff Tool Analysis\n\nBody.\n",
        encoding="utf-8",
    )
    site_content = tmp_path / "apps" / "site" / "src" / "content" / "docs"
    route_map = tmp_path / "apps" / "site" / "src" / "generated" / "docs-route-map.ts"
    monkeypatch.setattr(migrate, "ROOT", tmp_path)
    monkeypatch.setattr(migrate, "DOCS_SRC", docs_root)
    monkeypatch.setattr(migrate, "DOCS_DEST", site_content)
    monkeypatch.setattr(migrate, "ROUTE_MAP_DEST", route_map)

    migrate.main()

    index = (site_content / "tools" / "index.md").read_text(encoding="utf-8")
    ruff = (site_content / "tools" / "ruff.md").read_text(encoding="utf-8")
    config = (site_content / "usage" / "configuration.md").read_text(encoding="utf-8")
    assert_that(index).contains('title: "tools"')
    assert_that(ruff).contains('title: "ruff"')
    assert_that(ruff).contains("navGroup: python")
    assert_that(config).contains('title: "configuration"')
    assert_that(config).contains("navGroup: setup")
    assert_that(ruff.split("---")[1]).does_not_contain("Tool Analysis")
