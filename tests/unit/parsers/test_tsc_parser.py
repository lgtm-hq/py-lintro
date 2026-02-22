"""Unit tests for tsc parser."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.tsc.tsc_issue import TscIssue
from lintro.parsers.tsc.tsc_parser import (
    DEPENDENCY_ERROR_CODES,
    categorize_tsc_issues,
    extract_missing_modules,
    parse_tsc_output,
)


def test_parse_tsc_output_single_error() -> None:
    """Parse a single tsc error from text output."""
    output = (
        "src/main.ts(10,5): error TS2322:"
        " Type 'string' is not assignable to type 'number'."
    )
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/main.ts")
    assert_that(issues[0].line).is_equal_to(10)
    assert_that(issues[0].column).is_equal_to(5)
    assert_that(issues[0].code).is_equal_to("TS2322")
    assert_that(issues[0].severity).is_equal_to("error")
    assert_that(issues[0].message).contains("not assignable")


def test_parse_tsc_output_single_warning() -> None:
    """Parse a single tsc warning from text output."""
    output = (
        "src/utils.ts(15,1): warning TS6133:"
        " 'x' is declared but its value is never read."
    )
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to("warning")
    assert_that(issues[0].code).is_equal_to("TS6133")


def test_parse_tsc_output_multiple_errors() -> None:
    """Parse multiple errors from tsc output."""
    output = (
        "src/main.ts(10,5): error TS2322:"
        " Type 'string' is not assignable to type 'number'.\n"
        "src/main.ts(15,10): error TS2339:"
        " Property 'foo' does not exist on type 'Bar'.\n"
        "src/utils.ts(3,1): warning TS6133:"
        " 'x' is declared but its value is never read."
    )
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(3)
    assert_that(issues[0].code).is_equal_to("TS2322")
    assert_that(issues[1].code).is_equal_to("TS2339")
    assert_that(issues[2].code).is_equal_to("TS6133")
    assert_that(issues[2].severity).is_equal_to("warning")


def test_parse_tsc_output_mixed_with_non_errors() -> None:
    """Parse errors mixed with non-error output lines."""
    output = """Starting compilation...
src/main.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.
Processing files...
src/utils.ts(3,1): error TS2304: Cannot find name 'foo'.
Found 2 errors."""
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].file).is_equal_to("src/main.ts")
    assert_that(issues[1].file).is_equal_to("src/utils.ts")


def test_parse_tsc_output_empty() -> None:
    """Handle empty output."""
    assert_that(parse_tsc_output("")).is_empty()
    assert_that(parse_tsc_output("   \n\n  ")).is_empty()


def test_parse_tsc_output_windows_paths() -> None:
    """Normalize Windows backslashes to forward slashes."""
    output = r"src\components\Button.tsx(10,5): error TS2322: Type mismatch."
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/components/Button.tsx")


def test_parse_tsc_output_tsx_files() -> None:
    """Parse errors from TSX files."""
    output = (
        "src/components/Button.tsx(25,12): error TS2769: No overload matches this call."
    )
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/components/Button.tsx")


def test_parse_tsc_output_mts_cts_files() -> None:
    """Parse errors from .mts and .cts files."""
    output = """src/module.mts(5,1): error TS2322: Type error.
src/common.cts(10,1): error TS2322: Type error."""
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].file).is_equal_to("src/module.mts")
    assert_that(issues[1].file).is_equal_to("src/common.cts")


def test_parse_tsc_output_deep_nested_path() -> None:
    """Parse errors with deeply nested file paths."""
    output = (
        "packages/app/src/features/auth/hooks/useAuth.ts(42,15):"
        " error TS2345: Argument type mismatch."
    )
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to(
        "packages/app/src/features/auth/hooks/useAuth.ts",
    )


def test_parse_tsc_output_skips_non_matching_lines() -> None:
    """Skip non-matching lines gracefully."""
    output = """Starting compilation...
not valid tsc output
error TS6053: File not found."""
    issues = parse_tsc_output(output)

    assert_that(issues).is_empty()


# Tests for TscIssue.to_display_row


def test_tsc_issue_to_display_row() -> None:
    """Convert TscIssue to display row format."""
    issue = TscIssue(
        file="src/main.ts",
        line=10,
        column=5,
        code="TS2322",
        message="Type error",
        severity="error",
    )
    row = issue.to_display_row()

    assert_that(row["file"]).is_equal_to("src/main.ts")
    assert_that(row["line"]).is_equal_to("10")
    assert_that(row["column"]).is_equal_to("5")
    assert_that(row["code"]).is_equal_to("TS2322")
    assert_that(row["message"]).is_equal_to("Type error")
    assert_that(row["severity"]).is_equal_to("ERROR")


def test_tsc_issue_to_display_row_minimal() -> None:
    """Convert minimal TscIssue to display row format."""
    issue = TscIssue(file="main.ts", line=1, column=1, message="Error")
    row = issue.to_display_row()

    assert_that(row["file"]).is_equal_to("main.ts")
    assert_that(row["code"]).is_equal_to("")
    assert_that(row["severity"]).is_equal_to("WARNING")


def test_parse_tsc_output_ansi_codes_stripped() -> None:
    """Strip ANSI escape codes from output for consistent CI/local parsing."""
    # Output with ANSI color codes (common in CI environments)
    output = (
        "\x1b[31msrc/main.ts(10,5): error TS2322:"
        " Type 'string' is not assignable to type 'number'."
        "\x1b[0m"
    )
    issues = parse_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/main.ts")
    assert_that(issues[0].code).is_equal_to("TS2322")


# Tests for error categorization


def test_dependency_error_codes_contains_expected_codes() -> None:
    """DEPENDENCY_ERROR_CODES should contain expected TypeScript error codes."""
    assert_that(DEPENDENCY_ERROR_CODES).contains("TS2307")
    assert_that(DEPENDENCY_ERROR_CODES).contains("TS2688")
    assert_that(DEPENDENCY_ERROR_CODES).contains("TS7016")


def test_categorize_tsc_issues_separates_type_and_dep_errors() -> None:
    """Categorize issues into type errors and dependency errors."""
    issues = [
        TscIssue(
            file="src/main.ts",
            line=10,
            column=5,
            code="TS2322",
            message=("Type 'string' is not assignable" " to type 'number'."),
            severity="error",
        ),
        TscIssue(
            file="src/app.ts",
            line=1,
            column=1,
            code="TS2307",
            message=(
                "Cannot find module 'react'" " or its corresponding type declarations."
            ),
            severity="error",
        ),
        TscIssue(
            file="src/utils.ts",
            line=5,
            column=1,
            code="TS2688",
            message="Cannot find type definition file for 'node'.",
            severity="error",
        ),
    ]

    type_errors, dep_errors = categorize_tsc_issues(issues)

    assert_that(type_errors).is_length(1)
    assert_that(type_errors[0].code).is_equal_to("TS2322")
    assert_that(dep_errors).is_length(2)
    assert_that([e.code for e in dep_errors]).contains("TS2307", "TS2688")


def test_categorize_tsc_issues_all_type_errors() -> None:
    """All issues are type errors when no dependency codes present."""
    issues = [
        TscIssue(
            file="src/main.ts",
            line=10,
            column=5,
            code="TS2322",
            message="Type error",
            severity="error",
        ),
        TscIssue(
            file="src/main.ts",
            line=15,
            column=10,
            code="TS2339",
            message="Property does not exist",
            severity="error",
        ),
    ]

    type_errors, dep_errors = categorize_tsc_issues(issues)

    assert_that(type_errors).is_length(2)
    assert_that(dep_errors).is_empty()


def test_categorize_tsc_issues_all_dependency_errors() -> None:
    """All issues are dependency errors when all have dependency codes."""
    issues = [
        TscIssue(
            file="src/app.ts",
            line=1,
            column=1,
            code="TS2307",
            message="Cannot find module 'react'",
            severity="error",
        ),
        TscIssue(
            file="src/utils.ts",
            line=2,
            column=1,
            code="TS7016",
            message="Could not find declaration file for module 'lodash'.",
            severity="error",
        ),
    ]

    type_errors, dep_errors = categorize_tsc_issues(issues)

    assert_that(type_errors).is_empty()
    assert_that(dep_errors).is_length(2)


def test_categorize_tsc_issues_empty_list() -> None:
    """Handle empty issues list."""
    type_errors, dep_errors = categorize_tsc_issues([])

    assert_that(type_errors).is_empty()
    assert_that(dep_errors).is_empty()


def test_categorize_tsc_issues_no_code() -> None:
    """Issues without code are treated as type errors."""
    issues = [
        TscIssue(
            file="src/main.ts",
            line=10,
            column=5,
            code="",
            message="Some error",
            severity="error",
        ),
    ]

    type_errors, dep_errors = categorize_tsc_issues(issues)

    assert_that(type_errors).is_length(1)
    assert_that(dep_errors).is_empty()


# Tests for extract_missing_modules


def test_extract_missing_modules_from_ts2307() -> None:
    """Extract module names from TS2307 errors."""
    errors = [
        TscIssue(
            file="src/app.ts",
            line=1,
            column=1,
            code="TS2307",
            message=(
                "Cannot find module 'react'" " or its corresponding type declarations."
            ),
            severity="error",
        ),
        TscIssue(
            file="src/utils.ts",
            line=2,
            column=1,
            code="TS2307",
            message=(
                "Cannot find module '@types/node'"
                " or its corresponding type declarations."
            ),
            severity="error",
        ),
    ]

    modules = extract_missing_modules(errors)

    assert_that(modules).contains("react", "@types/node")
    assert_that(modules).is_length(2)


def test_extract_missing_modules_from_ts2688() -> None:
    """Extract type definition names from TS2688 errors."""
    errors = [
        TscIssue(
            file="src/app.ts",
            line=1,
            column=1,
            code="TS2688",
            message="Cannot find type definition file for 'node'.",
            severity="error",
        ),
    ]

    modules = extract_missing_modules(errors)

    assert_that(modules).contains("node")


def test_extract_missing_modules_from_ts7016() -> None:
    """Extract module names from TS7016 errors."""
    errors = [
        TscIssue(
            file="src/app.ts",
            line=1,
            column=1,
            code="TS7016",
            message="Could not find a declaration file for module 'lodash'.",
            severity="error",
        ),
    ]

    modules = extract_missing_modules(errors)

    assert_that(modules).contains("lodash")


def test_extract_missing_modules_deduplicates() -> None:
    """Extract unique module names when same module appears multiple times."""
    errors = [
        TscIssue(
            file="src/app.ts",
            line=1,
            column=1,
            code="TS2307",
            message="Cannot find module 'react'",
            severity="error",
        ),
        TscIssue(
            file="src/utils.ts",
            line=2,
            column=1,
            code="TS2307",
            message="Cannot find module 'react'",
            severity="error",
        ),
    ]

    modules = extract_missing_modules(errors)

    assert_that(modules).is_length(1)
    assert_that(modules).contains("react")


def test_extract_missing_modules_sorted() -> None:
    """Module names should be sorted alphabetically."""
    errors = [
        TscIssue(
            file="a.ts",
            line=1,
            column=1,
            code="TS2307",
            message="Cannot find module 'zod'",
            severity="error",
        ),
        TscIssue(
            file="b.ts",
            line=1,
            column=1,
            code="TS2307",
            message="Cannot find module 'axios'",
            severity="error",
        ),
        TscIssue(
            file="c.ts",
            line=1,
            column=1,
            code="TS2307",
            message="Cannot find module 'lodash'",
            severity="error",
        ),
    ]

    modules = extract_missing_modules(errors)

    assert_that(modules).is_equal_to(["axios", "lodash", "zod"])


def test_extract_missing_modules_empty_list() -> None:
    """Handle empty errors list."""
    modules = extract_missing_modules([])

    assert_that(modules).is_empty()


def test_extract_missing_modules_no_match() -> None:
    """Handle errors without recognizable module patterns."""
    errors = [
        TscIssue(
            file="a.ts",
            line=1,
            column=1,
            code="TS2307",
            message="Some other error format",
            severity="error",
        ),
    ]

    modules = extract_missing_modules(errors)

    assert_that(modules).is_empty()
