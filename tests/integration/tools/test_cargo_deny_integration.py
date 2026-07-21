"""Integration tests for cargo-deny tool definition.

These tests require cargo-deny and cargo to be installed and available in PATH.
They verify the CargoDenyPlugin definition, check command, and set_options method.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that
from packaging.version import Version

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


def _get_cargo_deny_version() -> Version | None:
    """Get the installed cargo-deny version.

    Returns:
        Version object or None if not installed or version cannot be determined.
    """
    if shutil.which("cargo") is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["cargo", "deny", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        match = re.search(r"(\d+\.\d+\.\d+)", result.stdout)
        if match:
            return Version(match.group(1))
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


_CARGO_DENY_MIN_VERSION = Version("0.14.0")
_installed_version = _get_cargo_deny_version()

# Skip all tests if cargo-deny is not installed or version is below minimum
pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None
    or _installed_version is None
    or _installed_version < _CARGO_DENY_MIN_VERSION,
    reason=f"cargo-deny >= {_CARGO_DENY_MIN_VERSION} or cargo not installed "
    f"(found: {_installed_version})",
)


# --- Tests for CargoDenyPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "cargo_deny"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify CargoDenyPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    plugin = get_plugin("cargo_deny")
    assert_that(getattr(plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify CargoDenyPlugin definition includes Cargo file patterns.

    Tests that the plugin is configured to handle Cargo.toml and deny.toml.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("cargo_deny")
    assert_that(plugin.definition.file_patterns).contains("Cargo.toml")
    assert_that(plugin.definition.file_patterns).contains("deny.toml")


# --- Integration tests for cargo-deny check command ---


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify cargo-deny check handles empty directories gracefully.

    Runs cargo-deny on an empty directory and verifies a result is returned
    with zero issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("cargo_deny")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_no_cargo_toml(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify cargo-deny check handles directories without Cargo.toml.

    Creates a directory with a deny.toml but no Cargo.toml and verifies
    cargo-deny skips gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    deny_toml = tmp_path / "deny.toml"
    deny_toml.write_text("[advisories]\n")

    plugin = get_plugin("cargo_deny")
    result = plugin.check([str(deny_toml)], {})

    assert_that(result).is_not_none()
    assert_that(result.output).contains("No Cargo.toml found")


# --- Tests for CargoDenyPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("timeout", 30, 30),
        ("timeout", 60, 60),
        ("timeout", 120, 120),
    ],
    ids=[
        "timeout_30",
        "timeout_60",
        "timeout_120",
    ],
)
def test_set_options_timeout(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify CargoDenyPlugin.set_options correctly sets timeout.

    Tests that plugin timeout option can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    plugin = get_plugin("cargo_deny")
    plugin.set_options(**{option_name: option_value})
    assert_that(plugin.options.get(option_name)).is_equal_to(expected)


def test_invalid_timeout(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify CargoDenyPlugin.set_options rejects invalid timeout values.

    Tests that invalid timeout values raise ValueError.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("cargo_deny")
    with pytest.raises(ValueError, match="must be positive"):
        plugin.set_options(timeout=-1)
