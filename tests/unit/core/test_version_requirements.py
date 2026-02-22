"""Tests for version requirements functionality."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from packaging.version import Version

if TYPE_CHECKING:
    pass

from lintro._tool_versions import TOOL_VERSIONS, get_min_version, get_tool_version
from lintro.enums.tool_name import ToolName, normalize_tool_name
from lintro.tools.core.version_parsing import (
    ToolVersionInfo,
    check_tool_version,
    compare_versions,
    extract_version_from_output,
    get_install_hints,
    get_minimum_versions,
    parse_version,
)
from lintro.tools.core.version_requirements import get_all_tool_versions


@pytest.mark.parametrize(
    "version_str,expected",
    [
        ("1.2.3", Version("1.2.3")),
        ("0.14.0", Version("0.14.0")),
        ("2.0", Version("2.0")),
        ("1.0.0-alpha", Version("1.0.0")),  # Pre-release suffix stripped
        ("v1.5.0", Version("1.5.0")),  # Handle leading 'v'
    ],
)
def test_parse_version(version_str: str, expected: Version) -> None:
    """Test version string parsing using packaging.version.

    Args:
        version_str: Version string to parse.
        expected: Expected parsed Version object.
    """
    assert_that(parse_version(version_str)).is_equal_to(expected)


def test_parse_version_invalid() -> None:
    """Test that parse_version raises ValueError for invalid input."""
    with pytest.raises(ValueError, match="Unable to parse version string"):
        parse_version("invalid")


@pytest.mark.parametrize(
    "version1,version2,expected",
    [
        ("1.2.3", "1.2.3", 0),  # Equal
        ("1.2.3", "1.2.4", -1),  # version1 < version2
        ("1.3.0", "1.2.4", 1),  # version1 > version2
        ("2.0.0", "1.9.9", 1),  # Major version difference
        ("1.10.0", "1.2.0", 1),  # Minor version difference
    ],
)
def test_compare_versions(version1: str, version2: str, expected: int) -> None:
    """Test version comparison.

    Args:
        version1: First version string to compare.
        version2: Second version string to compare.
        expected: Expected comparison result (-1, 0, or 1).
    """
    assert_that(compare_versions(version1, version2)).is_equal_to(expected)


@pytest.mark.parametrize(
    "tool_name,output,expected",
    [
        ("black", "black, 25.9.0 (compiled: yes)", "25.9.0"),
        ("bandit", "__main__.py 1.8.6", "1.8.6"),
        ("hadolint", "Haskell Dockerfile Linter 2.14.0", "2.14.0"),
        ("actionlint", "actionlint 1.7.5", "1.7.5"),
        ("pydoclint", "pydoclint 0.5.9", "0.5.9"),
        ("semgrep", "semgrep 1.148.0", "1.148.0"),
        ("ruff", "ruff 0.14.4", "0.14.4"),
        ("yamllint", "yamllint 1.37.1", "1.37.1"),
        # Astro: actual output has "v" prefix and double space
        ("astro-check", "astro  v5.5.3", "5.5.3"),
        # Vue-tsc: simple version output
        ("vue-tsc", "2.2.4", "2.2.4"),
        # Clippy: rustc output should extract Rust version directly
        ("clippy", "rustc 1.92.0 (ded5c06cf 2025-12-08)", "1.92.0"),
        # Clippy: clippy output should convert 0.1.X to 1.X.0
        ("clippy", "clippy 0.1.92 (ded5c06cf2 2025-12-08)", "1.92.0"),
        ("clippy", "clippy 0.1.75 (abcdef123 2024-01-01)", "1.75.0"),
    ],
)
def test_extract_version_from_output(
    tool_name: str,
    output: str,
    expected: str,
) -> None:
    """Test version extraction from various tool outputs.

    Args:
        tool_name: Name of the tool.
        output: Raw version output string from tool.
        expected: Expected extracted version string.
    """
    assert_that(extract_version_from_output(output, tool_name)).is_equal_to(expected)


def test_get_minimum_versions_from_tool_versions() -> None:
    """Test reading minimum versions from _tool_versions.py."""
    versions = get_minimum_versions()

    # Should include external tools from _tool_versions.py
    assert_that(versions).contains_key("hadolint")
    assert_that(versions).contains_key("actionlint")
    assert_that(versions).contains_key("pytest")
    assert_that(versions).contains_key("semgrep")

    # Versions should be strings
    assert_that(versions["hadolint"]).is_instance_of(str)
    assert_that(versions["actionlint"]).is_instance_of(str)


@pytest.mark.parametrize(
    "tool_name",
    [
        ToolName.ACTIONLINT,
        ToolName.HADOLINT,
        ToolName.OXLINT,
        ToolName.PRETTIER,
        ToolName.SEMGREP,
        ToolName.SHELLCHECK,
        ToolName.TSC,
    ],
)
def test_get_min_version_returns_version_for_registered_tools(
    tool_name: ToolName,
) -> None:
    """Test that get_min_version returns version for registered tools.

    Args:
        tool_name: ToolName enum member to test.
    """
    version = get_min_version(tool_name)
    assert_that(version).is_instance_of(str)
    assert_that(version).matches(r"^\d+\.\d+")  # Starts with X.Y


def test_get_min_version_raises_keyerror_for_unknown_tool() -> None:
    """Test that get_min_version raises KeyError for unknown tools."""
    with pytest.raises(KeyError, match="not found"):
        get_min_version(
            "nonexistent_tool",  # type: ignore[arg-type]
        )


def test_get_tool_version_supports_companion_packages() -> None:
    """Companion npm packages should resolve via get_tool_version."""
    repo_root = Path(__file__).resolve().parents[3]
    package_json = json.loads((repo_root / "package.json").read_text())
    expected = package_json["devDependencies"]["@astrojs/check"].lstrip("^~")

    version = get_tool_version("@astrojs/check")

    assert_that(version).is_equal_to(expected)


def test_tool_versions_uses_toolname_enum_keys() -> None:
    """Test that TOOL_VERSIONS uses ToolName enum as keys."""
    for key in TOOL_VERSIONS:
        assert_that(key).is_instance_of(ToolName)


def test_all_external_tools_registered_in_tool_versions() -> None:
    """Test that all expected external tools have versions available.

    npm-managed tools (markdownlint, oxfmt, oxlint, prettier, tsc) are
    read from package.json at runtime. Non-npm tools are in TOOL_VERSIONS.
    """
    from lintro._tool_versions import get_all_expected_versions

    repo_root = Path(__file__).resolve().parents[3]
    manifest_path = repo_root / "lintro" / "tools" / "manifest.json"

    # Non-npm tools should be in TOOL_VERSIONS
    expected_non_npm_tools = {
        ToolName.ACTIONLINT,
        ToolName.CARGO_AUDIT,
        ToolName.CLIPPY,
        ToolName.GITLEAKS,
        ToolName.HADOLINT,
        ToolName.PYTEST,
        ToolName.RUSTC,
        ToolName.RUSTFMT,
        ToolName.SEMGREP,
        ToolName.SHELLCHECK,
        ToolName.SHFMT,
        ToolName.SQLFLUFF,
        ToolName.TAPLO,
    }
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        manifest_tools = {
            normalize_tool_name(tool["name"])
            for tool in manifest.get("tools", [])
            if isinstance(tool, dict) and tool.get("name")
        }
        assert_that(set(TOOL_VERSIONS.keys())).is_subset_of(
            manifest_tools,
        )
    else:
        assert_that(set(TOOL_VERSIONS.keys())).is_equal_to(
            expected_non_npm_tools,
        )

    # All tools (including npm-managed) should be available
    # via get_all_expected_versions
    all_versions = get_all_expected_versions()
    if manifest_path.exists():
        assert_that(set(all_versions.keys())).is_equal_to(manifest_tools)
    else:
        expected_all_tools = {
            ToolName.ACTIONLINT,
            ToolName.ASTRO_CHECK,
            ToolName.CARGO_AUDIT,
            ToolName.CLIPPY,
            ToolName.GITLEAKS,
            ToolName.HADOLINT,
            ToolName.MARKDOWNLINT,
            ToolName.OXFMT,
            ToolName.OXLINT,
            ToolName.PRETTIER,
            ToolName.PYTEST,
            ToolName.RUSTC,
            ToolName.RUSTFMT,
            ToolName.SEMGREP,
            ToolName.SHELLCHECK,
            ToolName.SHFMT,
            ToolName.SQLFLUFF,
            ToolName.SVELTE_CHECK,
            ToolName.TAPLO,
            ToolName.TSC,
            ToolName.VUE_TSC,
        }
        assert_that(set(all_versions.keys())).is_equal_to(expected_all_tools)


def test_get_tool_version_returns_version_for_toolname_enum() -> None:
    """Test that get_tool_version works with ToolName enum."""
    version = get_tool_version(ToolName.TSC)
    assert_that(version).is_not_none()
    assert_that(version).is_instance_of(str)


def test_get_tool_version_typescript_alias_resolves_to_tsc() -> None:
    """Test that 'typescript' alias resolves to TSC version.

    This is important for shell script compatibility where the npm
    package name 'typescript' needs to resolve to the tsc version.
    """
    typescript_version = get_tool_version("typescript")
    tsc_version = get_tool_version(ToolName.TSC)
    assert_that(typescript_version).is_equal_to(tsc_version)
    assert_that(typescript_version).is_not_none()


def test_get_tool_version_returns_none_for_unknown_tool() -> None:
    """Test that get_tool_version returns None for unknown tools."""
    version = get_tool_version("nonexistent_tool")
    assert_that(version).is_none()


def test_get_install_hints() -> None:
    """Test generating install hints."""
    hints = get_install_hints()

    assert_that(hints).contains_key("pytest")
    assert_that(hints).contains_key("markdownlint")
    assert_that(hints["pytest"]).contains("Install via:")
    assert_that(hints["markdownlint"]).contains("bun add")


def test_version_caching() -> None:
    """Test that versions are cached properly."""
    # First call
    versions1 = get_minimum_versions()
    hints1 = get_install_hints()

    # Second call should return equal values (cached)
    versions2 = get_minimum_versions()
    hints2 = get_install_hints()

    assert_that(versions1).is_equal_to(versions2)
    assert_that(hints1).is_equal_to(hints2)


@patch("subprocess.run")
def test_check_tool_version_success(mock_run: MagicMock) -> None:
    """Test successful version check.

    Args:
        mock_run: Mocked subprocess.run function.
    """
    min_hadolint = get_minimum_versions()["hadolint"]
    mock_run.return_value = type(
        "MockResult",
        (),
        {
            "returncode": 0,
            "stdout": f"Haskell Dockerfile Linter {min_hadolint}",
            "stderr": "",
        },
    )()

    result = check_tool_version("hadolint", ["hadolint"])

    assert_that(result.name).is_equal_to("hadolint")
    assert_that(result.current_version).is_equal_to(min_hadolint)
    assert_that(result.min_version).is_equal_to(min_hadolint)
    assert_that(result.version_check_passed).is_true()
    assert_that(result.error_message).is_none()


@patch("subprocess.run")
def test_check_tool_version_hyphenated_alias_uses_requirements(
    mock_run: MagicMock,
) -> None:
    """Hyphenated tool names should resolve to canonical version requirements."""
    min_astro = get_minimum_versions()["astro_check"]
    mock_run.return_value = type(
        "MockResult",
        (),
        {
            "returncode": 0,
            "stdout": f"astro {min_astro}",
            "stderr": "",
        },
    )()

    result = check_tool_version("astro-check", ["bunx", "astro"])

    assert_that(result.name).is_equal_to("astro-check")
    assert_that(result.current_version).is_equal_to(min_astro)
    assert_that(result.min_version).is_equal_to(min_astro)
    assert_that(result.install_hint).contains("astro")
    assert_that(result.version_check_passed).is_true()


@patch("subprocess.run")
def test_check_tool_version_failure(mock_run: MagicMock) -> None:
    """Test version check that fails due to old version.

    Args:
        mock_run: Mocked subprocess.run function.
    """
    min_hadolint = get_minimum_versions()["hadolint"]
    mock_run.return_value = type(
        "MockResult",
        (),
        {
            "returncode": 0,
            "stdout": "Haskell Dockerfile Linter 0.0.0",  # Below any real minimum
            "stderr": "",
        },
    )()

    result = check_tool_version("hadolint", ["hadolint"])

    assert_that(result.name).is_equal_to("hadolint")
    assert_that(result.current_version).is_equal_to("0.0.0")
    assert_that(result.min_version).is_equal_to(min_hadolint)
    assert_that(result.version_check_passed).is_false()
    assert_that(result.error_message).contains("below minimum requirement")


@patch("subprocess.run")
def test_check_tool_version_command_failure(mock_run: MagicMock) -> None:
    """Test version check when command fails.

    Args:
        mock_run: Mocked subprocess.run function.
    """
    mock_run.side_effect = FileNotFoundError("Command not found")

    result = check_tool_version("nonexistent", ["nonexistent"])

    assert_that(result.name).is_equal_to("nonexistent")
    assert_that(result.current_version).is_none()
    # For tools not in requirements, version check passes (no enforcement)
    assert_that(result.version_check_passed).is_true()
    assert_that(result.error_message).is_not_none()
    assert_that(result.error_message).contains("Failed to run version check")


def test_tool_version_info_creation() -> None:
    """Test ToolVersionInfo dataclass."""
    info = ToolVersionInfo(
        name="test_tool",
        min_version="1.0.0",
        install_hint="Install test_tool",
        current_version="1.2.0",
        version_check_passed=True,
    )

    assert_that(info.name).is_equal_to("test_tool")
    assert_that(info.current_version).is_equal_to("1.2.0")
    assert_that(info.version_check_passed).is_true()


@patch("subprocess.run")
def test_get_all_tool_versions(mock_run: MagicMock) -> None:
    """Test getting versions for all tools.

    Args:
        mock_run: Mocked subprocess.run function.
    """
    # Mock successful version checks for all tools
    mock_run.return_value = type(
        "MockResult",
        (),
        {
            "returncode": 0,
            "stdout": "0.14.4",  # Generic version response
            "stderr": "",
        },
    )()

    results = get_all_tool_versions()

    # Should have results for all supported tools
    expected_tools = {
        "ruff",
        "black",
        "bandit",
        "yamllint",
        "sqlfluff",
        "mypy",
        "pytest",
        "pydoclint",
        "hadolint",
        "actionlint",
        "markdownlint",
        "clippy",
        "rustfmt",
        "semgrep",
        "gitleaks",
        "shellcheck",
        "shfmt",
        "taplo",
        "cargo_audit",
    }

    assert_that(set(results.keys())).is_equal_to(expected_tools)

    # Each result should be a ToolVersionInfo
    for tool_name, info in results.items():
        assert_that(info).is_instance_of(ToolVersionInfo)
        assert_that(info.name).is_equal_to(tool_name)
