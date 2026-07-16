"""Tests for lintro.parsers.base_issue module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pytest
from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


def test_base_issue_default_values() -> None:
    """BaseIssue has empty string and zero defaults."""
    issue = BaseIssue()
    assert_that(issue.file).is_equal_to("")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.message).is_equal_to("")


def test_base_issue_accepts_values() -> None:
    """BaseIssue accepts custom values."""
    issue = BaseIssue(file="test.py", line=10, column=5, message="Error found")
    assert_that(issue.file).is_equal_to("test.py")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.column).is_equal_to(5)
    assert_that(issue.message).is_equal_to("Error found")


def test_to_display_row_basic_fields() -> None:
    """to_display_row includes basic fields."""
    issue = BaseIssue(file="test.py", line=10, column=5, message="Test message")
    result = issue.to_display_row()
    assert_that(result["file"]).is_equal_to("test.py")
    assert_that(result["line"]).is_equal_to("10")
    assert_that(result["column"]).is_equal_to("5")
    assert_that(result["message"]).is_equal_to("Test message")


def test_to_display_row_zero_line_shows_dash() -> None:
    """to_display_row shows dash for zero line."""
    issue = BaseIssue(file="test.py", line=0, column=0)
    result = issue.to_display_row()
    assert_that(result["line"]).is_equal_to("-")
    assert_that(result["column"]).is_equal_to("-")


def test_to_display_row_missing_optional_fields() -> None:
    """to_display_row handles missing optional fields."""
    issue = BaseIssue()
    result = issue.to_display_row()
    assert_that(result["code"]).is_equal_to("")
    assert_that(result["severity"]).is_equal_to("WARNING")
    assert_that(result["fixable"]).is_equal_to("")


def test_display_field_map_class_variable() -> None:
    """BaseIssue has DISPLAY_FIELD_MAP class variable."""
    assert_that(BaseIssue.DISPLAY_FIELD_MAP).contains_key("code")
    assert_that(BaseIssue.DISPLAY_FIELD_MAP).contains_key("severity")
    assert_that(BaseIssue.DISPLAY_FIELD_MAP).contains_key("fixable")
    assert_that(BaseIssue.DISPLAY_FIELD_MAP).contains_key("message")


def test_subclass_with_custom_fields() -> None:
    """Subclass can add custom fields."""

    @dataclass
    class CustomIssue(BaseIssue):
        code: str = ""
        severity: str = ""

    issue = CustomIssue(
        file="test.py",
        line=1,
        column=1,
        message="Test",
        code="E001",
        severity="error",
    )
    result = issue.to_display_row()
    assert_that(result["code"]).is_equal_to("E001")
    assert_that(result["severity"]).is_equal_to("ERROR")


def test_subclass_with_custom_field_map() -> None:
    """Subclass can customize field mapping."""

    @dataclass
    class MappedIssue(BaseIssue):
        DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
            "code": "rule_id",
            "severity": "level",
            "fixable": "fixable",
            "message": "message",
        }
        rule_id: str = ""
        level: str = ""

    issue = MappedIssue(
        file="test.py",
        line=1,
        column=1,
        message="Test",
        rule_id="RULE001",
        level="warning",
    )
    result = issue.to_display_row()
    assert_that(result["code"]).is_equal_to("RULE001")
    assert_that(result["severity"]).is_equal_to("WARNING")


def test_to_display_row_fixable_true() -> None:
    """to_display_row shows Yes for fixable=True."""

    @dataclass
    class FixableIssue(BaseIssue):
        fixable: bool = False

    issue = FixableIssue(file="test.py", line=1, column=1, fixable=True)
    result = issue.to_display_row()
    assert_that(result["fixable"]).is_equal_to("Yes")


def test_to_display_row_fixable_false() -> None:
    """to_display_row shows empty string for fixable=False."""

    @dataclass
    class FixableIssue(BaseIssue):
        fixable: bool = False

    issue = FixableIssue(file="test.py", line=1, column=1, fixable=False)
    result = issue.to_display_row()
    assert_that(result["fixable"]).is_equal_to("")


@pytest.mark.parametrize(
    ("line", "column", "expected_line", "expected_column"),
    [
        (1, 1, "1", "1"),
        (100, 50, "100", "50"),
        (0, 5, "-", "5"),
        (10, 0, "10", "-"),
    ],
)
def test_to_display_row_line_column_formatting(
    line: int,
    column: int,
    expected_line: str,
    expected_column: str,
) -> None:
    """to_display_row formats line and column correctly.

    Args:
        line: The line number to test.
        column: The column number to test.
        expected_line: The expected line string in display row.
        expected_column: The expected column string in display row.
    """
    issue = BaseIssue(file="test.py", line=line, column=column)
    result = issue.to_display_row()
    assert_that(result["line"]).is_equal_to(expected_line)
    assert_that(result["column"]).is_equal_to(expected_column)


# =============================================================================
# Tests for get_severity()
# =============================================================================


def test_get_severity_returns_default_when_no_severity_field() -> None:
    """get_severity returns DEFAULT_SEVERITY when issue has no severity attr."""
    issue = BaseIssue(file="test.py", line=1)
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_get_severity_normalizes_string_field() -> None:
    """get_severity normalizes a native severity string."""

    @dataclass
    class SeverityIssue(BaseIssue):
        severity: str = ""

    issue = SeverityIssue(file="test.py", line=1, severity="error")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_get_severity_uses_display_field_map() -> None:
    """get_severity resolves the attribute via DISPLAY_FIELD_MAP."""

    @dataclass
    class MappedIssue(BaseIssue):
        DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
            **BaseIssue.DISPLAY_FIELD_MAP,
            "severity": "level",
        }
        level: str = ""

    issue = MappedIssue(file="test.py", line=1, level="warning")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_get_severity_falls_back_on_empty_string() -> None:
    """get_severity falls back to DEFAULT_SEVERITY for empty string."""

    @dataclass
    class SeverityIssue(BaseIssue):
        severity: str = ""

    issue = SeverityIssue(file="test.py", line=1, severity="")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_get_severity_falls_back_on_unknown_value() -> None:
    """get_severity falls back to DEFAULT_SEVERITY for unrecognized strings."""

    @dataclass
    class SeverityIssue(BaseIssue):
        severity: str = ""

    issue = SeverityIssue(file="test.py", line=1, severity="banana")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_get_severity_respects_custom_default() -> None:
    """get_severity uses subclass DEFAULT_SEVERITY."""

    @dataclass
    class InfoIssue(BaseIssue):
        DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.INFO

    issue = InfoIssue(file="test.py", line=1)
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.INFO)


def test_get_severity_passes_through_enum_instance() -> None:
    """get_severity returns SeverityLevel instances unchanged."""

    @dataclass
    class EnumIssue(BaseIssue):
        severity: SeverityLevel = SeverityLevel.ERROR

    issue = EnumIssue(file="test.py", line=1)
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


# =============================================================================
# Tests for get_code() / resolve_issue_code()
# =============================================================================


def test_get_code_returns_empty_when_no_code_field() -> None:
    """get_code returns empty string when issue has no code attr."""
    issue = BaseIssue(file="test.py", line=1)
    assert_that(issue.get_code()).is_equal_to("")


def test_get_code_reads_native_code_field() -> None:
    """get_code returns the native code attribute when mapped to code."""

    @dataclass
    class CodedIssue(BaseIssue):
        code: str = ""

    issue = CodedIssue(file="test.py", line=1, code="E501")
    assert_that(issue.get_code()).is_equal_to("E501")


def test_get_code_uses_display_field_map() -> None:
    """get_code resolves the attribute via DISPLAY_FIELD_MAP."""

    @dataclass
    class MappedIssue(BaseIssue):
        DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
            **BaseIssue.DISPLAY_FIELD_MAP,
            "code": "rule",
        }
        rule: str | None = None

    issue = MappedIssue(file="bad.yml", line=1, rule="document-start")
    assert_that(issue.get_code()).is_equal_to("document-start")


def test_get_code_falls_back_on_empty_mapped_value() -> None:
    """get_code returns empty string when the mapped attribute is empty/None."""

    @dataclass
    class MappedIssue(BaseIssue):
        DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
            **BaseIssue.DISPLAY_FIELD_MAP,
            "code": "rule",
        }
        rule: str | None = None

    issue = MappedIssue(file="bad.yml", line=1, rule=None)
    assert_that(issue.get_code()).is_equal_to("")


def test_resolve_issue_code_prefers_get_code() -> None:
    """resolve_issue_code uses get_code for DISPLAY_FIELD_MAP aliases."""
    from lintro.parsers.base_issue import resolve_issue_code

    @dataclass
    class MappedIssue(BaseIssue):
        DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
            **BaseIssue.DISPLAY_FIELD_MAP,
            "code": "rule",
        }
        rule: str = "colons"

    issue = MappedIssue(file="bad.yml", line=1, rule="colons")
    assert_that(resolve_issue_code(issue)).is_equal_to("colons")


def test_resolve_issue_code_falls_back_to_raw_code_attr() -> None:
    """resolve_issue_code falls back to a raw code attribute for duck types."""
    from lintro.parsers.base_issue import resolve_issue_code

    class DuckIssue:
        code = "F401"

    assert_that(resolve_issue_code(DuckIssue())).is_equal_to("F401")


def test_resolve_issue_code_ignores_non_string_get_code_return() -> None:
    """resolve_issue_code ignores MagicMock-style get_code return values."""
    from unittest.mock import MagicMock

    from lintro.parsers.base_issue import resolve_issue_code

    issue = MagicMock()
    issue.code = "E501"
    # MagicMock auto-creates a callable get_code that returns another mock.
    assert_that(resolve_issue_code(issue)).is_equal_to("E501")


def test_resolve_issue_code_preserves_zero_raw_code() -> None:
    """resolve_issue_code preserves a duck-typed numeric code of 0."""
    from lintro.parsers.base_issue import resolve_issue_code

    class DuckIssue:
        code = 0

    assert_that(resolve_issue_code(DuckIssue())).is_equal_to("0")


def test_get_code_preserves_zero_mapped_value() -> None:
    """get_code preserves a mapped numeric code of 0."""

    @dataclass
    class MappedIssue(BaseIssue):
        DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
            **BaseIssue.DISPLAY_FIELD_MAP,
            "code": "rule",
        }
        rule: int | None = None

    issue = MappedIssue(file="x.py", line=1, rule=0)
    assert_that(issue.get_code()).is_equal_to("0")
