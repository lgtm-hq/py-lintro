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
    assert spec and spec.loader
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
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "category: getting-started" in text
    assert "# Getting Started" in text


def test_main_writes_route_map(isolated_docs: tuple[Any, Path]) -> None:
    """Migration should emit a source→doc route map for the site link layer."""
    migrate, site_content = isolated_docs
    migrate.main()
    route_map = (site_content.parents[1] / "generated" / "docs-route-map.ts").read_text(
        encoding="utf-8"
    )
    assert_that(route_map).contains("export const sourceToDoc")
    assert_that(route_map).contains(
        '"getting-started.md": "getting-started/getting-started",',
    )


def test_docs_paths_use_repo_layout() -> None:
    """Default paths should target py-lintro docs and site content."""
    migrate = _load_migrate_module()
    assert migrate.DOCS_SRC.name == "docs"
    assert migrate.DOCS_DEST.parts[-3:] == ("src", "content", "docs")


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
    assert 'title: "tools"' in index
    assert 'title: "ruff"' in ruff
    assert "navGroup: python" in ruff
    assert 'title: "configuration"' in config
    assert "navGroup: setup" in config
    assert "Tool Analysis" not in ruff.split("---")[1]
