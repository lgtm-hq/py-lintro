"""Unit tests for vue-tsc parser."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.vue_tsc.vue_tsc_issue import VueTscIssue
from lintro.parsers.vue_tsc.vue_tsc_parser import (
    categorize_vue_tsc_issues,
    extract_missing_modules,
    parse_vue_tsc_output,
)


def test_parse_vue_tsc_output_empty() -> None:
    """Handle empty output."""
    assert_that(parse_vue_tsc_output("")).is_empty()
    assert_that(parse_vue_tsc_output("   \n\n  ")).is_empty()


def test_parse_vue_tsc_output_single_error() -> None:
    """Parse a single vue-tsc error from text output."""
    output = (
        "src/App.vue(10,5): error TS2322: "
        "Type 'string' is not assignable to type 'number'."
    )
    issues = parse_vue_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/App.vue")
    assert_that(issues[0].line).is_equal_to(10)
    assert_that(issues[0].column).is_equal_to(5)
    assert_that(issues[0].code).is_equal_to("TS2322")
    assert_that(issues[0].severity).is_equal_to("error")
    assert_that(issues[0].message).contains("not assignable")


def test_parse_vue_tsc_output_multiple_errors() -> None:
    """Parse multiple errors from vue-tsc output."""
    output = (
        "src/App.vue(10,5): error TS2322:"
        " Type 'string' is not assignable"
        " to type 'number'.\n"
        "src/components/Card.vue(15,10): error TS2339:"
        " Property 'foo' does not exist"
        " on type 'Props'.\n"
        "src/views/Home.vue(3,1): warning TS6133:"
        " 'x' is declared but its value"
        " is never read."
    )
    issues = parse_vue_tsc_output(output)

    assert_that(issues).is_length(3)
    assert_that(issues[0].code).is_equal_to("TS2322")
    assert_that(issues[1].code).is_equal_to("TS2339")
    assert_that(issues[2].code).is_equal_to("TS6133")
    assert_that(issues[2].severity).is_equal_to("warning")


def test_parse_vue_tsc_output_windows_paths() -> None:
    """Normalize Windows backslashes to forward slashes."""
    output = r"src\components\Button.vue(10,5): error TS2322: Type mismatch."
    issues = parse_vue_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/components/Button.vue")


def test_parse_vue_tsc_output_ansi_codes() -> None:
    """Strip ANSI escape codes from output."""
    output = "\x1b[31msrc/App.vue(10,5): error TS2322: Type mismatch.\x1b[0m"
    issues = parse_vue_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/App.vue")


def test_parse_vue_tsc_output_skips_noise_lines() -> None:
    """Skip non-error lines like summary and progress."""
    output = """Checking types...
src/App.vue(10,5): error TS2322: Type mismatch.
Found 1 error in 1 file."""
    issues = parse_vue_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/App.vue")


def test_vue_tsc_issue_type() -> None:
    """Verify parsed issues are VueTscIssue instances."""
    output = "src/App.vue(10,5): error TS2322: Type error."
    issues = parse_vue_tsc_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0]).is_instance_of(VueTscIssue)


def test_categorize_vue_tsc_issues_all_type_errors() -> None:
    """Categorize issues with only type errors."""
    issues = [
        VueTscIssue(
            file="src/App.vue",
            line=10,
            column=5,
            code="TS2322",
            severity="error",
            message="Type mismatch.",
        ),
    ]

    type_errors, dependency_errors = categorize_vue_tsc_issues(issues)

    assert_that(type_errors).is_length(1)
    assert_that(dependency_errors).is_empty()


def test_categorize_vue_tsc_issues_dependency_errors() -> None:
    """Categorize issues with dependency errors."""
    issues = [
        VueTscIssue(
            file="src/App.vue",
            line=1,
            column=1,
            code="TS2307",
            severity="error",
            message="Cannot find module 'vue' or its corresponding type declarations.",
        ),
        VueTscIssue(
            file="src/App.vue",
            line=10,
            column=5,
            code="TS2322",
            severity="error",
            message="Type mismatch.",
        ),
    ]

    type_errors, dependency_errors = categorize_vue_tsc_issues(issues)

    assert_that(type_errors).is_length(1)
    assert_that(dependency_errors).is_length(1)
    assert_that(dependency_errors[0].code).is_equal_to("TS2307")


def test_extract_missing_modules() -> None:
    """Extract module names from dependency errors."""
    dependency_errors = [
        VueTscIssue(
            file="src/App.vue",
            line=1,
            column=1,
            code="TS2307",
            severity="error",
            message="Cannot find module 'vue' or its corresponding type declarations.",
        ),
        VueTscIssue(
            file="src/App.vue",
            line=2,
            column=1,
            code="TS2307",
            severity="error",
            message=(
                "Cannot find module '@vueuse/core'"
                " or its corresponding"
                " type declarations."
            ),
        ),
    ]

    modules = extract_missing_modules(dependency_errors)

    assert_that(modules).is_length(2)
    assert_that(modules).contains("@vueuse/core")
    assert_that(modules).contains("vue")


def test_vue_tsc_issue_to_display_row() -> None:
    """Convert VueTscIssue to display row format."""
    issue = VueTscIssue(
        file="src/App.vue",
        line=10,
        column=5,
        code="TS2322",
        message="Type error",
        severity="error",
    )
    row = issue.to_display_row()

    assert_that(row["file"]).is_equal_to("src/App.vue")
    assert_that(row["line"]).is_equal_to("10")
    assert_that(row["column"]).is_equal_to("5")
    assert_that(row["code"]).is_equal_to("TS2322")
    assert_that(row["message"]).is_equal_to("Type error")
    assert_that(row["severity"]).is_equal_to("ERROR")


def test_vue_tsc_issue_to_display_row_minimal() -> None:
    """Convert minimal VueTscIssue to display row format."""
    issue = VueTscIssue(file="main.vue", line=1, column=1, message="Error")
    row = issue.to_display_row()

    assert_that(row["file"]).is_equal_to("main.vue")
    assert_that(row["code"]).is_equal_to("")
    assert_that(row["severity"]).is_equal_to("WARNING")
    assert_that(row["fixable"]).is_equal_to("")
