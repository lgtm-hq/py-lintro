"""Unit tests for the cppcheck XML parser.

The XML samples below are real cppcheck output (schema version 2) captured from
seeded C fixtures, trimmed to the relevant ``<error>`` entries.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.cppcheck.cppcheck_issue import CppcheckIssue
from lintro.parsers.cppcheck.cppcheck_parser import parse_cppcheck_output

# Real captured cppcheck output covering error/warning/style severities, a CWE
# id, a multi-location value-flow trace, and a <symbol> element.
SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<results version="2">
    <errors>
        <error id="arrayIndexOutOfBounds" severity="error" msg="Array 'buffer[5]' accessed at index 10, which is out of bounds." verbose="Array 'buffer[5]' accessed at index 10, which is out of bounds." cwe="788" file0="violations.c">
            <location file="violations.c" line="16" column="11" info="Array index out of bounds"/>
        </error>
        <error id="memleak" severity="error" msg="Memory leak: data" verbose="Memory leak: data" cwe="401" file0="violations.c">
            <location file="violations.c" line="22" column="5"/>
            <symbol>data</symbol>
        </error>
        <error id="nullPointerOutOfMemory" severity="warning" msg="If memory allocation fails, then there is a possible null pointer dereference: data" verbose="..." cwe="476" file0="violations.c">
            <location file="violations.c" line="21" column="5" info="Null pointer dereference"/>
            <location file="violations.c" line="20" column="17" info="Assignment"/>
            <location file="violations.c" line="20" column="30" info="Assuming allocation function fails"/>
            <symbol>data</symbol>
        </error>
        <error id="unassignedVariable" severity="style" msg="Variable 'value' is not assigned a value." verbose="..." cwe="665" file0="violations.c">
            <location file="violations.c" line="10" column="9"/>
            <symbol>value</symbol>
        </error>
    </errors>
</results>"""

EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<results version="2">
    <errors>
    </errors>
</results>"""


def test_parse_returns_all_errors() -> None:
    """All <error> entries are parsed into issues."""
    result = parse_cppcheck_output(SAMPLE_XML)
    assert_that(result).is_length(4)
    assert_that(result[0]).is_instance_of(CppcheckIssue)


def test_parse_extracts_all_fields() -> None:
    """The first error's fields are extracted correctly."""
    issue = parse_cppcheck_output(SAMPLE_XML)[0]
    assert_that(issue.file).is_equal_to("violations.c")
    assert_that(issue.line).is_equal_to(16)
    assert_that(issue.column).is_equal_to(11)
    assert_that(issue.code).is_equal_to("arrayIndexOutOfBounds")
    assert_that(issue.severity).is_equal_to("error")
    assert_that(issue.cwe).is_equal_to(788)
    assert_that(issue.inconclusive).is_false()
    assert_that(issue.message).contains("out of bounds")


def test_parse_uses_first_location_for_traces() -> None:
    """For multi-location traces, the primary (first) location is reported."""
    # nullPointerOutOfMemory is the third error and has three locations.
    issue = parse_cppcheck_output(SAMPLE_XML)[2]
    assert_that(issue.code).is_equal_to("nullPointerOutOfMemory")
    assert_that(issue.line).is_equal_to(21)
    assert_that(issue.column).is_equal_to(5)


@pytest.mark.parametrize(
    "index,severity",
    [
        (0, "error"),
        (2, "warning"),
        (3, "style"),
    ],
)
def test_parse_preserves_native_severity(index: int, severity: str) -> None:
    """Native cppcheck severities are preserved verbatim (no collapsing).

    Args:
        index: Index of the error in SAMPLE_XML.
        severity: Expected native severity string.
    """
    result = parse_cppcheck_output(SAMPLE_XML)
    assert_that(result[index].severity).is_equal_to(severity)


def test_style_severity_normalizes_to_info() -> None:
    """A 'style' finding normalizes to INFO for display."""
    issue = parse_cppcheck_output(SAMPLE_XML)[3]
    assert_that(str(issue.get_severity())).is_equal_to("INFO")


def test_parse_empty_results_returns_empty() -> None:
    """A report with no errors yields an empty list."""
    assert_that(parse_cppcheck_output(EMPTY_XML)).is_empty()


@pytest.mark.parametrize(
    "value",
    [None, "", "   \n  "],
    ids=["none", "empty", "whitespace"],
)
def test_parse_invalid_input_returns_empty(value: str | None) -> None:
    """None, empty, or whitespace-only input returns an empty list.

    Args:
        value: The input to parse.
    """
    assert_that(parse_cppcheck_output(value)).is_empty()


def test_parse_malformed_xml_returns_empty() -> None:
    """Malformed XML (no closing tags) returns an empty list."""
    assert_that(parse_cppcheck_output("<results><errors><error")).is_empty()


def test_parse_non_xml_text_returns_empty() -> None:
    """Text without a <results> block returns an empty list."""
    assert_that(parse_cppcheck_output("Checking foo.c ...\nnope")).is_empty()


def test_parse_extracts_results_from_surrounding_noise() -> None:
    """A <results> block embedded in other text is still parsed."""
    noisy = f"Checking violations.c ...\n{SAMPLE_XML}\ndone"
    assert_that(parse_cppcheck_output(noisy)).is_length(4)


def test_parse_inconclusive_flag() -> None:
    """The inconclusive attribute is captured when present."""
    xml = """<results version="2"><errors>
        <error id="uninitvar" severity="error" msg="Uninitialized variable: a" inconclusive="true" cwe="457" file0="a.c">
            <location file="a.c" line="5" column="10"/>
        </error>
    </errors></results>"""
    issue = parse_cppcheck_output(xml)[0]
    assert_that(issue.inconclusive).is_true()


def test_parse_error_without_location() -> None:
    """A meta error without a <location> falls back to file0 and line 0."""
    xml = """<results version="2"><errors>
        <error id="missingInclude" severity="information" msg="Cppcheck cannot find all the include files." file0="a.c">
        </error>
    </errors></results>"""
    issue = parse_cppcheck_output(xml)[0]
    assert_that(issue.file).is_equal_to("a.c")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.severity).is_equal_to("information")


def test_parse_missing_cwe_defaults_zero() -> None:
    """An error without a cwe attribute defaults cwe to 0."""
    xml = """<results version="2"><errors>
        <error id="syntaxError" severity="error" msg="syntax error" file0="a.c">
            <location file="a.c" line="1" column="1"/>
        </error>
    </errors></results>"""
    issue = parse_cppcheck_output(xml)[0]
    assert_that(issue.cwe).is_equal_to(0)
