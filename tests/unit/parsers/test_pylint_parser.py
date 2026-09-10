"""Unit tests for the pylint ``json2`` output parser."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.pylint import PylintIssue, parse_pylint_output

EMPTY_OUTPUT = json.dumps(
    {
        "messages": [],
        "statistics": {
            "messageTypeCount": {
                "fatal": 0,
                "error": 0,
                "warning": 0,
                "refactor": 0,
                "convention": 0,
                "info": 0,
            },
            "modulesLinted": 2,
            "score": 10.0,
        },
    },
)

#: The R0801 body pylint emits for a clone set spanning two files. The
#: ``==module:[start:end]`` header lines and the quoted source are part of the
#: message, not decoration around it.
R0801_MESSAGE = (
    "Similar lines in 2 files\n"
    "==first:[12:27]\n"
    "==second:[12:27]\n"
    "    totals = []\n"
    "    for index, entry in enumerate(records):\n"
    "        if entry is None:\n"
    "            continue\n"
    "    return totals"
)

DUPLICATE_OUTPUT = json.dumps(
    {
        "messages": [
            {
                "type": "refactor",
                "symbol": "duplicate-code",
                "message": R0801_MESSAGE,
                "messageId": "R0801",
                "confidence": "UNDEFINED",
                "module": "second",
                "obj": "",
                "line": 1,
                "column": 0,
                "endLine": None,
                "endColumn": None,
                "path": "second.py",
                "absolutePath": "/repo/second.py",
            },
        ],
        "statistics": {"modulesLinted": 2, "score": 9.5},
    },
)

MIXED_OUTPUT = json.dumps(
    {
        "messages": [
            {
                "type": "convention",
                "symbol": "missing-module-docstring",
                "message": "Missing module docstring",
                "messageId": "C0114",
                "line": 1,
                "column": 0,
                "path": "pkg/a.py",
                "absolutePath": "/repo/pkg/a.py",
            },
            {
                "type": "error",
                "symbol": "undefined-variable",
                "message": "Undefined variable 'missing'",
                "messageId": "E0602",
                "line": 7,
                "column": 11,
                "path": "pkg/b.py",
                "absolutePath": "/repo/pkg/b.py",
            },
            {
                "type": "refactor",
                "symbol": "duplicate-code",
                "message": R0801_MESSAGE,
                "messageId": "R0801",
                "line": 1,
                "column": 0,
                "path": "pkg/b.py",
                "absolutePath": "/repo/pkg/b.py",
            },
        ],
        "statistics": {"modulesLinted": 2, "score": 4.0},
    },
)


@pytest.mark.parametrize("output", [None, "", "   ", "\n"])
def test_empty_input_yields_no_issues(output: str | None) -> None:
    """Blank output parses to an empty issue list rather than raising.

    Args:
        output: Blank or missing tool output.
    """
    assert_that(parse_pylint_output(output)).is_empty()


def test_clean_report_yields_no_issues() -> None:
    """A json2 report with an empty ``messages`` array yields no issues."""
    assert_that(parse_pylint_output(EMPTY_OUTPUT)).is_empty()


def test_duplicate_code_message_is_parsed() -> None:
    """An R0801 message maps onto every PylintIssue field."""
    issues = parse_pylint_output(DUPLICATE_OUTPUT)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue).is_instance_of(PylintIssue)
    assert_that(issue.file).is_equal_to("second.py")
    assert_that(issue.line).is_equal_to(1)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.code).is_equal_to("R0801")
    assert_that(issue.symbol).is_equal_to("duplicate-code")
    assert_that(issue.message_type).is_equal_to("refactor")


def test_duplicate_code_message_body_is_kept_verbatim() -> None:
    """The multi-line R0801 body survives parsing unmodified.

    The body is the only description of *what* is duplicated: the participating
    modules, their line ranges, and the duplicated source. Collapsing or
    trimming it would leave the finding uninterpretable, so assert byte
    equality rather than a substring.
    """
    issue = parse_pylint_output(DUPLICATE_OUTPUT)[0]

    assert_that(issue.message).is_equal_to(R0801_MESSAGE)
    assert_that(issue.message).contains("==first:[12:27]", "==second:[12:27]")


def test_mixed_report_preserves_order_and_codes() -> None:
    """A report mixing categories yields one issue per message, in order."""
    issues = parse_pylint_output(MIXED_OUTPUT)

    assert_that(issues).is_length(3)
    assert_that([issue.code for issue in issues]).is_equal_to(
        ["C0114", "E0602", "R0801"],
    )
    assert_that([issue.file for issue in issues]).is_equal_to(
        ["pkg/a.py", "pkg/b.py", "pkg/b.py"],
    )
    assert_that(issues[1].line).is_equal_to(7)
    assert_that(issues[1].column).is_equal_to(11)


@pytest.mark.parametrize(
    ("message_type", "expected"),
    [
        ("fatal", SeverityLevel.ERROR),
        ("error", SeverityLevel.ERROR),
        ("warning", SeverityLevel.WARNING),
        ("refactor", SeverityLevel.WARNING),
        ("convention", SeverityLevel.INFO),
        ("info", SeverityLevel.INFO),
    ],
)
def test_message_type_drives_the_severity(
    message_type: str,
    expected: SeverityLevel,
) -> None:
    """Pylint's category is translated into a normalized severity.

    Without this mapping every message would land on the inherited WARNING
    default, flattening a syntax error and a naming convention into one level.

    Args:
        message_type: pylint message category.
        expected: Severity the category must resolve to.
    """
    output = json.dumps(
        {
            "messages": [
                {
                    "type": message_type,
                    "symbol": "some-symbol",
                    "message": "text",
                    "messageId": "X0000",
                    "line": 1,
                    "column": 0,
                    "path": "a.py",
                },
            ],
        },
    )

    assert_that(parse_pylint_output(output)[0].get_severity()).is_equal_to(expected)


def test_unknown_message_type_falls_back_to_warning() -> None:
    """An unrecognized category uses the class default rather than crashing."""
    output = json.dumps(
        {"messages": [{"type": "novel", "messageId": "X1", "path": "a.py"}]},
    )

    assert_that(parse_pylint_output(output)[0].get_severity()).is_equal_to(
        SeverityLevel.WARNING,
    )


def test_duplicate_code_is_a_warning_not_an_info() -> None:
    """R0801 is a ``refactor``; it must not be demoted below WARNING.

    ``duplicate-code`` is the reason this plugin exists, so burying it in the
    informational bucket would defeat the point of wrapping pylint at all.
    """
    issue = parse_pylint_output(DUPLICATE_OUTPUT)[0]

    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_display_row_exposes_the_message_id_as_the_code() -> None:
    """The unified display row renders ``messageId`` in the code column."""
    row = parse_pylint_output(DUPLICATE_OUTPUT)[0].to_display_row()

    assert_that(row["code"]).is_equal_to("R0801")
    assert_that(row["file"]).is_equal_to("second.py")
    assert_that(row["fixable"]).is_equal_to("")


def test_missing_messages_key_raises() -> None:
    """A JSON object with no ``messages`` array is not a json2 report.

    Every report carries the array, empty on a clean run, so its absence means
    the output could not be read — which must never be reported as clean.
    """
    with pytest.raises(json.JSONDecodeError):
        parse_pylint_output(json.dumps({"statistics": {}}))


def test_non_object_messages_are_skipped() -> None:
    """Entries that are not objects are dropped instead of crashing the run."""
    output = json.dumps({"messages": ["not-a-message", None, 7]})

    assert_that(parse_pylint_output(output)).is_empty()


def test_message_without_a_path_is_skipped() -> None:
    """A message naming no file cannot be located, so it is dropped.

    ``absolutePath`` is accepted as a fallback, so only an entry carrying
    neither is skipped.
    """
    output = json.dumps(
        {
            "messages": [
                {"messageId": "R0801", "symbol": "duplicate-code", "line": 1},
                {
                    "messageId": "R0801",
                    "symbol": "duplicate-code",
                    "absolutePath": "/repo/c.py",
                    "line": 2,
                },
            ],
        },
    )

    issues = parse_pylint_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("/repo/c.py")


def test_invalid_json_raises() -> None:
    """Unparseable output raises so the caller can never report a clean pass.

    Swallowing this would turn a broken pylint run into a green result, which
    is exactly the failure mode #1044 guards against for security tooling.
    """
    with pytest.raises(json.JSONDecodeError):
        parse_pylint_output("pylint: error: unrecognized arguments")


def test_json_array_output_raises() -> None:
    """The legacy ``json`` reporter's array shape is rejected, not parsed.

    Accepting it silently would hide a misconfigured ``--output-format``.
    """
    with pytest.raises(json.JSONDecodeError):
        parse_pylint_output(json.dumps([{"messageId": "R0801"}]))
