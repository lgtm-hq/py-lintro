"""Unit tests for ManifestRegistry manifest loading and query methods."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.core.tool_registry import (
    ManifestRegistry,
    ManifestTool,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def v2_manifest_path(tmp_path: Path) -> Path:
    """Copy the v2 manifest fixture to a temp directory and return its path.

    Args:
        tmp_path: Pytest built-in temporary directory.

    Returns:
        Path to the copied manifest.json.
    """
    src = _FIXTURE_DIR / "test_manifest.json"
    dest = tmp_path / "manifest.json"
    shutil.copy2(src, dest)
    return dest


@pytest.fixture()
def registry(v2_manifest_path: Path) -> ManifestRegistry:
    """Load a ManifestRegistry from the v2 test manifest.

    Args:
        v2_manifest_path: Path to the test manifest.

    Returns:
        ManifestRegistry loaded from the test manifest.
    """
    return ManifestRegistry._load_from_path(v2_manifest_path)


# ===========================================================================
# Manifest loading
# ===========================================================================


def test_load_v2_manifest(registry: ManifestRegistry) -> None:
    """Loading a v2 manifest populates tools, profiles, and language_map."""
    assert_that(len(registry)).is_equal_to(6)
    assert_that(registry.profile_names).is_length(4)
    assert_that(registry.language_map).contains_key("python", "docker", "security")


def test_load_v1_compat(tmp_path: Path) -> None:
    """A v1 manifest without top-level version_command falls back to install block."""
    manifest = {
        "version": 1,
        "tools": [
            {
                "name": "old-tool",
                "version": "1.0.0",
                "install": {
                    "type": "pip",
                    "version_command": ["old-tool", "-V"],
                },
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    reg = ManifestRegistry._load_from_path(path)
    tool = reg.get("old-tool")
    assert_that(tool.version_command).is_equal_to(("old-tool", "-V"))
    assert_that(tool.category).is_equal_to("bundled")


def test_load_missing_name_skips_tool(tmp_path: Path) -> None:
    """A tool entry without a 'name' field is silently skipped."""
    manifest = {
        "version": 2,
        "tools": [
            {"version": "1.0.0", "install": {"type": "pip"}},
            {
                "name": "good",
                "version": "1.0.0",
                "install": {"type": "pip"},
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    reg = ManifestRegistry._load_from_path(path)
    assert_that(len(reg)).is_equal_to(1)
    assert_that("good" in reg).is_true()


def test_load_missing_version_skips_tool(tmp_path: Path) -> None:
    """A tool entry without a 'version' field is silently skipped."""
    manifest = {
        "version": 2,
        "tools": [
            {"name": "no-ver", "install": {"type": "pip"}},
            {
                "name": "good",
                "version": "2.0.0",
                "install": {"type": "pip"},
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    reg = ManifestRegistry._load_from_path(path)
    assert_that(len(reg)).is_equal_to(1)
    assert_that("no-ver" in reg).is_false()


def test_load_invalid_manifest_version(tmp_path: Path) -> None:
    """A non-integer manifest version raises ValueError."""
    manifest = {"version": "not_int", "tools": []}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert_that(ManifestRegistry._load_from_path).raises(ValueError).when_called_with(
        path,
    ).is_equal_to("manifest 'version' must be an integer, got 'not_int'")


def test_parse_tool_entry_invalid_version_command(tmp_path: Path) -> None:
    """A non-list version_command is treated as an empty list."""
    manifest = {
        "version": 2,
        "tools": [
            {
                "name": "bad-cmd",
                "version": "1.0.0",
                "install": {"type": "pip"},
                "version_command": "not-a-list",
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    reg = ManifestRegistry._load_from_path(path)
    tool = reg.get("bad-cmd")
    assert_that(tool.version_command).is_equal_to(())


# ===========================================================================
# all_tools
# ===========================================================================


def test_all_tools_excludes_dev_by_default(registry: ManifestRegistry) -> None:
    """Dev-tier tools are excluded when include_dev is False (default)."""
    tools = registry.all_tools()
    names = [t.name for t in tools]
    assert_that(names).does_not_contain("dev-tool")


def test_all_tools_includes_dev(registry: ManifestRegistry) -> None:
    """Dev-tier tools are included when include_dev is True."""
    tools = registry.all_tools(include_dev=True)
    names = [t.name for t in tools]
    assert_that(names).contains("dev-tool")


def test_all_tools_sorted_by_name(registry: ManifestRegistry) -> None:
    """Returned tools are sorted alphabetically by name."""
    tools = registry.all_tools(include_dev=True)
    names = [t.name for t in tools]
    assert_that(names).is_equal_to(sorted(names))


# ===========================================================================
# tools_for_languages
# ===========================================================================


def test_tools_for_languages_single(registry: ManifestRegistry) -> None:
    """Passing ['python'] returns python-mapped tools plus security."""
    tools = registry.tools_for_languages(["python"])
    names = [t.name for t in tools]
    assert_that(names).contains("ruff", "mypy", "gitleaks")


def test_tools_for_languages_multiple(registry: ManifestRegistry) -> None:
    """Passing multiple languages returns the union of their tool sets."""
    tools = registry.tools_for_languages(["python", "docker"])
    names = [t.name for t in tools]
    assert_that(names).contains("ruff", "mypy", "hadolint", "gitleaks")


def test_tools_for_languages_always_includes_security(
    registry: ManifestRegistry,
) -> None:
    """Security tools are always included regardless of language list."""
    tools = registry.tools_for_languages(["docker"])
    names = [t.name for t in tools]
    assert_that(names).contains("gitleaks")


def test_tools_for_languages_unknown_lang(registry: ManifestRegistry) -> None:
    """An unknown language returns only security tools."""
    tools = registry.tools_for_languages(["cobol"])
    names = [t.name for t in tools]
    assert_that(names).is_equal_to(["gitleaks"])


# ===========================================================================
# tools_for_profile
# ===========================================================================


def test_tools_for_profile_explicit(registry: ManifestRegistry) -> None:
    """The 'minimal' explicit profile returns exactly the listed tools."""
    tools = registry.tools_for_profile("minimal")
    names = [t.name for t in tools]
    assert_that(names).is_equal_to(["mypy", "ruff"])


def test_tools_for_profile_auto_detect_with_langs(
    registry: ManifestRegistry,
) -> None:
    """The 'recommended' profile with detected_langs delegates to tools_for_languages."""
    tools = registry.tools_for_profile("recommended", detected_langs=["python"])
    names = [t.name for t in tools]
    assert_that(names).contains("ruff", "mypy", "gitleaks")


def test_tools_for_profile_auto_detect_no_langs_falls_back_to_minimal(
    registry: ManifestRegistry,
) -> None:
    """The 'recommended' profile without detected languages falls back to 'minimal'."""
    tools = registry.tools_for_profile("recommended")
    names = [t.name for t in tools]
    assert_that(names).is_equal_to(["mypy", "ruff"])


def test_tools_for_profile_all(registry: ManifestRegistry) -> None:
    """The 'complete' profile returns every tool, including dev."""
    tools = registry.tools_for_profile("complete")
    names = [t.name for t in tools]
    assert_that(names).contains("dev-tool")
    assert_that(len(names)).is_equal_to(6)


def test_tools_for_profile_filter_excludes_formatters(
    registry: ManifestRegistry,
) -> None:
    """The 'ci' profile excludes tools whose tags are a subset of ['formatter'].

    'ruff' has tags ['linter', 'formatter'] so it is kept.
    'black' has tags ['formatter'] which is a subset — excluded.
    """
    tools = registry.tools_for_profile(
        "ci",
        detected_langs=["python"],
    )
    names = [t.name for t in tools]
    assert_that(names).contains("ruff")
    assert_that(names).does_not_contain("black")


def test_tools_for_profile_unknown_raises(registry: ManifestRegistry) -> None:
    """An unknown profile name raises KeyError."""
    assert_that(registry.tools_for_profile).raises(KeyError).when_called_with(
        "nonexistent",
    )


# ===========================================================================
# Query methods
# ===========================================================================


def test_get_existing_tool(registry: ManifestRegistry) -> None:
    """get() returns the ManifestTool for a known tool name."""
    tool = registry.get("ruff")
    assert_that(tool).is_instance_of(ManifestTool)
    assert_that(tool.name).is_equal_to("ruff")
    assert_that(tool.version).is_equal_to("0.14.0")


def test_get_missing_tool_raises_key_error(registry: ManifestRegistry) -> None:
    """get() raises KeyError for an unknown tool name."""
    assert_that(registry.get).raises(KeyError).when_called_with("nope")


def test_get_or_none_existing(registry: ManifestRegistry) -> None:
    """get_or_none() returns the ManifestTool when the tool exists."""
    tool = registry.get_or_none("mypy")
    assert_that(tool).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert tool is not None  # noqa: S101
    assert_that(tool.name).is_equal_to("mypy")


def test_get_or_none_missing(registry: ManifestRegistry) -> None:
    """get_or_none() returns None when the tool does not exist."""
    tool = registry.get_or_none("nope")
    assert_that(tool).is_none()


def test_contains_true(registry: ManifestRegistry) -> None:
    """__contains__ returns True for a registered tool."""
    assert_that("ruff" in registry).is_true()


def test_contains_false(registry: ManifestRegistry) -> None:
    """__contains__ returns False for an unregistered tool."""
    assert_that("nope" in registry).is_false()


def test_len(registry: ManifestRegistry) -> None:
    """__len__ returns the total number of tools in the registry."""
    assert_that(len(registry)).is_equal_to(6)


# ===========================================================================
# ToolRegistry rename (#1220)
# ===========================================================================


def test_manifest_registry_importable() -> None:
    """ManifestRegistry is importable from lintro.tools.core.tool_registry."""
    from lintro.tools.core.tool_registry import ManifestRegistry

    assert_that(isinstance(ManifestRegistry, type)).is_true()


def test_plugin_tool_registry_distinct() -> None:
    """The plugin and manifest registries are different, unrelated classes."""
    from lintro.plugins.registry import ToolRegistry as PluginToolRegistry
    from lintro.tools.core.tool_registry import ManifestRegistry

    assert_that(PluginToolRegistry).is_not_equal_to(ManifestRegistry)
    assert_that(PluginToolRegistry.__name__).is_not_equal_to(
        ManifestRegistry.__name__,
    )
