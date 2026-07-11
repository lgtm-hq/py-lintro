"""Unit tests for rustc version resolution and parsing."""

from __future__ import annotations

from assertpy import assert_that

from lintro._tool_versions import TOOL_VERSIONS, get_min_version
from lintro.enums.tool_name import ToolName
from lintro.tools.core.version_checking import get_install_hints
from lintro.tools.core.version_parsing import extract_version_from_output


def test_rustc_registered_in_tool_versions() -> None:
    """RUSTC has a pinned entry in TOOL_VERSIONS."""
    assert_that(TOOL_VERSIONS).contains_key(ToolName.RUSTC)


def test_get_min_version_returns_version_string() -> None:
    """get_min_version resolves a dotted version string for rustc."""
    version = get_min_version(ToolName.RUSTC)
    assert_that(version).is_instance_of(str)
    assert_that(version).matches(r"^\d+\.\d+")


def test_extract_version_from_rustc_output() -> None:
    """extract_version_from_output parses rustc's version banner."""
    output = "rustc 1.92.0 (ded5c06cf 2025-12-08)"
    version = extract_version_from_output(output, "rustc")
    assert_that(version).is_equal_to("1.92.0")


def test_extract_version_from_rustc_output_ignores_case() -> None:
    """Rustc version extraction is case-insensitive on the prefix."""
    output = "RUSTC 1.80.1 (abcdef123 2024-05-01)"
    version = extract_version_from_output(output, "rustc")
    assert_that(version).is_equal_to("1.80.1")


def test_rustc_has_install_hint() -> None:
    """The install hints map includes rustup guidance for rustc."""
    hints = get_install_hints()
    assert_that(hints).contains_key("rustc")
    assert_that(hints["rustc"]).contains("rustup")
