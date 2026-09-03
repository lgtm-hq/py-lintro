"""The homepage tool catalog must match the builtin tool registry."""

from __future__ import annotations

import re
from pathlib import Path

from assertpy import assert_that

from lintro.tools.core.tool_manager import ToolManager

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "apps" / "site" / "src" / "data" / "tools-catalog.ts"

# Registry names that are not products a visitor installs; they have no card.
NON_CATALOG_TOOLS: frozenset[str] = frozenset()

# Catalog cards that bundle several registry entries under one product name.
CATALOG_ALIASES: dict[str, set[str]] = {}


def _catalog_names() -> set[str]:
    """Return the tool names declared in the site catalog.

    Returns:
        Every ``tool(<name>, …)`` entry in ``tools-catalog.ts``.
    """
    source = CATALOG.read_text(encoding="utf-8")
    return set(re.findall(r"^\s*tool\('([^']+)'", source, re.MULTILINE))


def _registry_names() -> set[str]:
    """Return registry tool names in their CLI (hyphenated) form.

    Returns:
        Registered plugin names with underscores normalised to hyphens.
    """
    manager = ToolManager()
    return {name.replace("_", "-") for name in manager.get_all_tools()}


def test_site_catalog_matches_registry() -> None:
    """Every registered tool has a homepage card, and no card is orphaned."""
    catalog = _catalog_names()
    registry = _registry_names() - NON_CATALOG_TOOLS
    for alias, members in CATALOG_ALIASES.items():
        if alias in catalog:
            registry -= members
            registry.add(alias)

    assert_that(sorted(catalog - registry)).is_equal_to([])
    assert_that(sorted(registry - catalog)).is_equal_to([])
