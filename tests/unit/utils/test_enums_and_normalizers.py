"""Tests for enums and normalizer functions."""

from assertpy import assert_that

from lintro.enums.group_by import GroupBy, normalize_group_by
from lintro.enums.hadolint_enums import (
    HadolintFailureThreshold,
    HadolintFormat,
    normalize_hadolint_format,
    normalize_hadolint_threshold,
)
from lintro.enums.output_format import OutputFormat, normalize_output_format
from lintro.enums.tool_name import ToolName, normalize_tool_name
from lintro.enums.yamllint_format import YamllintFormat, normalize_yamllint_format


def test_output_format_normalization() -> None:
    """Normalize output format strings and enum instances consistently."""
    assert_that(normalize_output_format("grid")).is_equal_to(OutputFormat.GRID)
    assert_that(normalize_output_format(OutputFormat.JSON)).is_equal_to(
        OutputFormat.JSON,
    )
    assert_that(normalize_output_format("unknown")).is_equal_to(OutputFormat.GRID)


def test_group_by_normalization() -> None:
    """Normalize group-by strings and enum instances consistently."""
    assert_that(normalize_group_by("file")).is_equal_to(GroupBy.FILE)
    assert_that(normalize_group_by(GroupBy.AUTO)).is_equal_to(GroupBy.AUTO)
    assert_that(normalize_group_by("category")).is_equal_to(GroupBy.CATEGORY)
    assert_that(normalize_group_by(GroupBy.CATEGORY)).is_equal_to(GroupBy.CATEGORY)
    assert_that(normalize_group_by("bad")).is_equal_to(GroupBy.FILE)


def test_tool_name_normalization() -> None:
    """Normalize tool names from strings and enum instances."""
    assert_that(normalize_tool_name("ruff")).is_equal_to(ToolName.RUFF)
    assert_that(normalize_tool_name(ToolName.RUFF)).is_equal_to(ToolName.RUFF)


def test_yamllint_format_normalization() -> None:
    """Normalize yamllint format values from strings and enums."""
    assert_that(normalize_yamllint_format("parsable")).is_equal_to(
        YamllintFormat.PARSABLE,
    )
    assert_that(normalize_yamllint_format(YamllintFormat.GITHUB)).is_equal_to(
        YamllintFormat.GITHUB,
    )


def test_hadolint_normalization() -> None:
    """Normalize hadolint format and threshold string values."""
    assert_that(normalize_hadolint_format("json")).is_equal_to(HadolintFormat.JSON)
    assert_that(normalize_hadolint_threshold("warning")).is_equal_to(
        HadolintFailureThreshold.WARNING,
    )
    assert_that(normalize_hadolint_format("bogus")).is_equal_to(HadolintFormat.TTY)
    assert_that(normalize_hadolint_threshold("bogus")).is_equal_to(
        HadolintFailureThreshold.INFO,
    )


def test_tool_config_info_reexport() -> None:
    """Test that tool_config_info re-exports get_tool_config_summary."""
    from lintro.utils import tool_config_info

    assert_that(hasattr(tool_config_info, "get_tool_config_summary")).is_true()
    assert_that("get_tool_config_summary" in tool_config_info.__all__).is_true()
