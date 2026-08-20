"""Unit tests for rustc version resolution and parsing."""

from __future__ import annotations

from assertpy import assert_that

from lintro._tool_versions import TOOL_VERSIONS, get_min_version
from lintro.enums.tool_name import ToolName
from lintro.tools.core.version_checking import get_install_hints
from lintro.tools.core.version_parsing import extract_version_from_output


def test_rustc_registered_in_tool_versions() -> None:
    """RUSTC has a dotted three-part pin in TOOL_VERSIONS."""
    assert_that(TOOL_VERSIONS).contains_key(ToolName.RUSTC)
    assert_that(TOOL_VERSIONS[ToolName.RUSTC]).matches(r"^\d+\.\d+\.\d+$")


def test_get_min_version_returns_the_pinned_version() -> None:
    """get_min_version resolves the same pin TOOL_VERSIONS stores."""
    version = get_min_version(ToolName.RUSTC)
    assert_that(version).is_equal_to(TOOL_VERSIONS[ToolName.RUSTC])


def test_extract_version_from_rustc_output() -> None:
    """extract_version_from_output parses rustc's version banner."""
    pin = TOOL_VERSIONS[ToolName.RUSTC]
    output = f"rustc {pin} (ded5c06cf 2025-12-08)"
    version = extract_version_from_output(output, "rustc")
    assert_that(version).is_equal_to(pin)


def test_extract_version_from_rustc_output_ignores_case() -> None:
    """Rustc version extraction is case-insensitive on the prefix."""
    output = "RUSTC 1.80.1 (abcdef123 2024-05-01)"
    version = extract_version_from_output(output, "rustc")
    assert_that(version).is_equal_to("1.80.1")


def test_rustc_has_install_hint() -> None:
    """The install hints map includes rustup guidance for rustc."""
    hints = get_install_hints()
    pin = TOOL_VERSIONS[ToolName.RUSTC]
    assert_that(hints).contains_key("rustc")
    assert_that(hints["rustc"]).is_equal_to(
        f"Install via: rustup toolchain install {pin} && rustup default {pin}",
    )
