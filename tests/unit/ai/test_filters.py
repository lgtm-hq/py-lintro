"""Tests for AI issue filtering (path/rule allow/deny policy)."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.filters import filter_issues, should_process_issue
from lintro.parsers.base_issue import BaseIssue
from tests.unit.ai.conftest import MockIssue

# -- should_process_issue: no filters -----------------------------------------


def test_no_filters_allows_all() -> None:
    """With no filters configured, all issues pass."""
    config = AIConfig()
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_true()


def test_no_filters_allows_empty_fields() -> None:
    """Issues with empty file/code pass when no filters are set."""
    config = AIConfig()
    issue = MockIssue(file="", code="")
    assert_that(should_process_issue(issue, config)).is_true()


# -- include_paths -------------------------------------------------------------


def test_include_paths_matches() -> None:
    """Issue with matching path is included."""
    config = AIConfig(include_paths=["src/*.py"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_true()


def test_include_paths_no_match() -> None:
    """Issue with non-matching path is excluded."""
    config = AIConfig(include_paths=["src/*.py"])
    issue = MockIssue(file="tests/test_main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


def test_include_paths_multiple_patterns() -> None:
    """Issue matching any include pattern is included."""
    config = AIConfig(include_paths=["src/*.py", "lib/*.py"])
    issue = MockIssue(file="lib/utils.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_true()


def test_include_paths_glob_star_star() -> None:
    """Recursive glob pattern matches nested paths."""
    config = AIConfig(include_paths=["src/**/*.py"])
    issue = MockIssue(file="src/deep/nested/module.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_true()


# -- exclude_paths -------------------------------------------------------------


def test_exclude_paths_matches() -> None:
    """Issue with matching exclude path is rejected."""
    config = AIConfig(exclude_paths=["tests/*"])
    issue = MockIssue(file="tests/test_main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


def test_exclude_paths_no_match() -> None:
    """Issue not matching exclude path is allowed."""
    config = AIConfig(exclude_paths=["tests/*"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_true()


def test_exclude_paths_multiple_patterns() -> None:
    """Issue matching any exclude pattern is rejected."""
    config = AIConfig(exclude_paths=["tests/*", "docs/*"])
    issue = MockIssue(file="docs/readme.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


# -- include_paths + exclude_paths together ------------------------------------


def test_include_and_exclude_paths_include_wins_when_not_excluded() -> None:
    """Include passes, exclude does not match: issue is processed."""
    config = AIConfig(include_paths=["src/*.py"], exclude_paths=["src/vendor/*"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_true()


def test_include_and_exclude_paths_exclude_overrides() -> None:
    """Include passes but exclude also matches: issue is rejected."""
    config = AIConfig(include_paths=["src/*"], exclude_paths=["src/vendor/*"])
    issue = MockIssue(file="src/vendor/lib.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


def test_include_paths_rejects_before_exclude_checked() -> None:
    """If include_paths doesn't match, exclude_paths is irrelevant."""
    config = AIConfig(include_paths=["lib/*"], exclude_paths=["tests/*"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


# -- include_rules -------------------------------------------------------------


def test_include_rules_matches() -> None:
    """Issue with matching rule code is included."""
    config = AIConfig(include_rules=["E5*"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_true()


def test_include_rules_no_match() -> None:
    """Issue with non-matching rule code is excluded."""
    config = AIConfig(include_rules=["E5*"])
    issue = MockIssue(file="src/main.py", code="W123")
    assert_that(should_process_issue(issue, config)).is_false()


def test_include_rules_exact_match() -> None:
    """Exact rule code match works."""
    config = AIConfig(include_rules=["B101"])
    issue = MockIssue(file="src/main.py", code="B101")
    assert_that(should_process_issue(issue, config)).is_true()


# -- exclude_rules -------------------------------------------------------------


def test_exclude_rules_matches() -> None:
    """Issue with matching exclude rule is rejected."""
    config = AIConfig(exclude_rules=["E501"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


def test_exclude_rules_no_match() -> None:
    """Issue not matching exclude rule is allowed."""
    config = AIConfig(exclude_rules=["E501"])
    issue = MockIssue(file="src/main.py", code="B101")
    assert_that(should_process_issue(issue, config)).is_true()


def test_exclude_rules_glob_pattern() -> None:
    """Glob pattern in exclude_rules matches multiple codes."""
    config = AIConfig(exclude_rules=["E*"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


# -- include_rules + exclude_rules together ------------------------------------


def test_include_and_exclude_rules_together() -> None:
    """Include passes but exclude also matches: issue is rejected."""
    config = AIConfig(include_rules=["E*"], exclude_rules=["E501"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


def test_include_rules_passes_exclude_rules_does_not_match() -> None:
    """Include passes, exclude does not match: issue is processed."""
    config = AIConfig(include_rules=["E*"], exclude_rules=["E501"])
    issue = MockIssue(file="src/main.py", code="E302")
    assert_that(should_process_issue(issue, config)).is_true()


# -- Combined path and rule filters -------------------------------------------


def test_path_and_rule_filters_both_pass() -> None:
    """Both path and rule filters must pass for issue to be processed."""
    config = AIConfig(include_paths=["src/*"], include_rules=["E*"])
    issue = MockIssue(file="src/main.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_true()


def test_path_passes_rule_fails() -> None:
    """Path passes but rule does not: issue is rejected."""
    config = AIConfig(include_paths=["src/*"], include_rules=["E*"])
    issue = MockIssue(file="src/main.py", code="B101")
    assert_that(should_process_issue(issue, config)).is_false()


def test_rule_passes_path_fails() -> None:
    """Rule passes but path does not: issue is rejected."""
    config = AIConfig(include_paths=["src/*"], include_rules=["E*"])
    issue = MockIssue(file="tests/test.py", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()


# -- filter_issues -------------------------------------------------------------


def test_filter_issues_returns_matching_only() -> None:
    """filter_issues returns only issues that pass the filter."""
    config = AIConfig(include_paths=["src/*"])
    issues: list[BaseIssue] = [
        MockIssue(file="src/main.py", code="E501"),
        MockIssue(file="tests/test.py", code="E501"),
        MockIssue(file="src/utils.py", code="B101"),
    ]
    result = filter_issues(issues, config)
    assert_that(result).is_length(2)
    assert_that([i.file for i in result]).contains("src/main.py", "src/utils.py")


def test_filter_issues_empty_list() -> None:
    """filter_issues handles empty list."""
    config = AIConfig(include_paths=["src/*"])
    result = filter_issues([], config)
    assert_that(result).is_empty()


def test_filter_issues_no_filters() -> None:
    """filter_issues with no filters returns all issues."""
    config = AIConfig()
    issues: list[BaseIssue] = [
        MockIssue(file="src/main.py", code="E501"),
        MockIssue(file="tests/test.py", code="B101"),
    ]
    result = filter_issues(issues, config)
    assert_that(result).is_length(2)


def test_filter_issues_all_excluded() -> None:
    """filter_issues can exclude all issues."""
    config = AIConfig(exclude_paths=["*"])
    issues: list[BaseIssue] = [
        MockIssue(file="src/main.py", code="E501"),
        MockIssue(file="tests/test.py", code="B101"),
    ]
    result = filter_issues(issues, config)
    assert_that(result).is_empty()


# -- Edge cases ----------------------------------------------------------------


def test_issue_without_code_attribute() -> None:
    """BaseIssue without code attribute is handled gracefully."""
    config = AIConfig(include_rules=["E*"])
    issue = BaseIssue(file="src/main.py", line=1, message="test")
    # BaseIssue has no code attr, getattr returns ""
    assert_that(should_process_issue(issue, config)).is_false()


def test_issue_without_file() -> None:
    """Issue with empty file and include_paths filter is excluded."""
    config = AIConfig(include_paths=["src/*"])
    issue = MockIssue(file="", code="E501")
    assert_that(should_process_issue(issue, config)).is_false()
