"""Tests for lint bridge integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.review import lint_bridge
from lintro.ai.review.lint_bridge import (
    LintReportError,
    format_lint_results_for_prompt,
    load_lint_report,
    restrict_lint_results_to_files,
    run_lint_on_changed_files,
)
from lintro.config.lintro_config import LintroConfig
from lintro.models.core.tool_result import ToolResult


def test_run_lint_on_changed_files_returns_empty_for_no_paths() -> None:
    """No changed files yields an empty lint result list."""
    results = run_lint_on_changed_files(
        changed_files=[],
        lintro_config=LintroConfig(),
    )

    assert_that(results).is_empty()


def test_format_lint_results_for_prompt_returns_empty_without_issues() -> None:
    """Empty lint results produce an empty prompt section."""
    digest = format_lint_results_for_prompt(
        results=[ToolResult(name="ruff", success=True, issues_count=0, issues=[])],
    )

    assert_that(digest).is_empty()


def test_format_lint_results_for_prompt_wraps_issue_lines() -> None:
    """Lint issues are formatted as compact digest lines."""
    issue = MagicMock()
    issue.code = "E501"
    issue.message = "Line too long"
    issue.file = "src/main.py"
    issue.line = 10

    digest = format_lint_results_for_prompt(
        results=[
            ToolResult(
                name="ruff",
                success=False,
                issues_count=1,
                issues=[issue],
            ),
        ],
    )

    assert_that(digest).contains("<lint_results>")
    assert_that(digest).contains("Tool: ruff")
    assert_that(digest).contains("E501")


def test_run_lint_on_changed_files_invokes_tool_check() -> None:
    """Lint bridge runs configured tools against changed file paths."""
    mock_tool = MagicMock()
    mock_tool.check.return_value = ToolResult(name="ruff", success=True, issues_count=0)

    with patch(
        "lintro.ai.review.lint_bridge.get_tools_to_run",
    ) as mock_get_tools:
        mock_get_tools.return_value.to_run = ["ruff", "black"]
        with (
            patch(
                "lintro.ai.review.lint_bridge.tool_manager.get_tool",
                return_value=mock_tool,
            ),
            patch(
                "lintro.ai.review.lint_bridge.configure_tool_for_execution",
                side_effect=lambda *, tool, **kwargs: tool,
            ) as mock_configure,
        ):
            results = run_lint_on_changed_files(
                changed_files=["src/main.py"],
                lintro_config=LintroConfig(),
            )

    assert_that(results).is_length(2)
    # Format authority is resolved from the run's selection (#1742), so the
    # bridge must hand over the tools it is actually running — an empty set
    # would leave ruff formatting alongside black.
    for call in mock_configure.call_args_list:
        assert_that(call.kwargs["selected_tools"]).is_equal_to({"ruff", "black"})


def test_run_lint_on_changed_files_returns_empty_when_selection_fails() -> None:
    """A failure selecting tools drops the digest instead of aborting (#2571).

    ``get_tools_to_run`` triggers plugin discovery; if that raises, the review
    must still run from the diff alone.
    """
    with patch(
        "lintro.ai.review.lint_bridge.get_tools_to_run",
        side_effect=RuntimeError("discovery exploded"),
    ):
        results = run_lint_on_changed_files(
            changed_files=["src/main.py"],
            lintro_config=LintroConfig(),
        )

    assert_that(results).is_empty()


def test_run_lint_on_changed_files_returns_empty_when_config_manager_fails() -> None:
    """A failure loading native tool configs drops the digest (#2571).

    ``UnifiedConfigManager`` reads pyproject and native tool configs from disk;
    a malformed file must not take the review down with it.
    """
    with (
        patch("lintro.ai.review.lint_bridge.get_tools_to_run") as mock_get_tools,
        patch(
            "lintro.ai.review.lint_bridge.UnifiedConfigManager",
            side_effect=ValueError("bad pyproject"),
        ),
        patch("lintro.ai.review.lint_bridge.tool_manager.get_tool") as mock_get_tool,
    ):
        mock_get_tools.return_value.to_run = ["ruff"]
        results = run_lint_on_changed_files(
            changed_files=["src/main.py"],
            lintro_config=LintroConfig(),
        )

    assert_that(results).is_empty()
    mock_get_tool.assert_not_called()


def _write_report(path: Path, results: object) -> Path:
    """Write a minimal lintro JSON report and return its path.

    Args:
        path: Destination file.
        results: Value for the report's ``results`` field.

    Returns:
        The written path.
    """
    path.write_text(
        json.dumps({"action": "check", "summary": {}, "results": results}),
        encoding="utf-8",
    )
    return path


def test_load_lint_report_rehydrates_tool_results(tmp_path: Path) -> None:
    """A saved report loads into the shape ``run_lint_on_changed_files`` returns."""
    report = _write_report(
        tmp_path / "results.json",
        [
            {
                "tool": "ruff",
                "success": False,
                "issues_count": 1,
                "issues": [
                    {"file": "src/main.py", "line": 3, "code": "F401", "message": "x"},
                ],
            },
            {"tool": "black", "success": True, "issues_count": 0},
        ],
    )

    results = load_lint_report(report)

    assert_that(results).is_length(2)
    assert_that(results[0].name).is_equal_to("ruff")
    assert_that(results[0].success).is_false()
    assert_that(results[0].issues_count).is_equal_to(1)
    issue = (results[0].issues or [])[0]
    assert_that(issue).is_instance_of(lint_bridge.LintReportIssue)
    assert_that(issue.file).is_equal_to("src/main.py")
    assert_that(issue.line).is_equal_to(3)
    assert_that(getattr(issue, "code", None)).is_equal_to("F401")
    assert_that(issue.message).is_equal_to("x")
    assert_that(results[1].issues).is_empty()
    # The rehydrated results feed the prompt formatter unchanged.
    digest = format_lint_results_for_prompt(results=results)
    assert_that(digest).contains("Tool: ruff | file: src/main.py | line: 3")
    assert_that(digest).contains("Code: F401 | Message: x")


def test_load_lint_report_rejects_a_missing_file(tmp_path: Path) -> None:
    """A path that does not exist is an error the caller turns into a note."""
    assert_that(load_lint_report).raises(LintReportError).when_called_with(
        tmp_path / "absent.json",
    )


def test_load_lint_report_rejects_invalid_json(tmp_path: Path) -> None:
    """Truncated or non-JSON content is an error, not a crash."""
    report = tmp_path / "results.json"
    report.write_text('{"results": [', encoding="utf-8")

    assert_that(load_lint_report).raises(LintReportError).when_called_with(report)


@pytest.mark.parametrize(
    "results",
    [
        "not a list",
        [{"success": True}],
        [{"tool": "", "issues": []}],
        [{"tool": "ruff", "issues": "nope"}],
        [{"tool": "ruff", "issues": [{"line": 1}]}],
        [{"tool": "ruff", "issues": [{"file": "a.py", "message": "m", "line": "3"}]}],
    ],
    ids=[
        "results-not-a-list",
        "entry-without-tool",
        "empty-tool-name",
        "issues-not-a-list",
        "issue-without-file",
        "issue-with-string-line",
    ],
)
def test_load_lint_report_rejects_malformed_shapes(
    tmp_path: Path,
    results: object,
) -> None:
    """Anything not shaped like a lintro report is refused rather than coerced.

    Args:
        tmp_path: Pytest temporary directory.
        results: Malformed ``results`` value.
    """
    report = _write_report(tmp_path / "results.json", results)

    assert_that(load_lint_report).raises(LintReportError).when_called_with(report)


def test_load_lint_report_rejects_an_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report is PR-controlled data, so it has a byte ceiling before parsing.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    report = _write_report(tmp_path / "results.json", [])
    monkeypatch.setattr(lint_bridge, "MAX_LINT_REPORT_BYTES", 4)

    assert_that(load_lint_report).raises(LintReportError).when_called_with(report)


def _issue(file: str) -> lint_bridge.LintReportIssue:
    """Build a report issue whose message names its file.

    Args:
        file: Path as the report would write it.

    Returns:
        The issue.
    """
    return lint_bridge.LintReportIssue(file=file, line=1, message=file)


def _kept(results: list[ToolResult]) -> list[str]:
    """Return the messages of every issue kept on the first result.

    Args:
        results: Restricted tool results.

    Returns:
        Issue messages in order.
    """
    return [issue.message for issue in results[0].issues or []]


def test_restrict_lint_results_to_files_keeps_only_changed_files() -> None:
    """Issues on files outside the review are dropped and counts recomputed.

    Relative report paths match exactly (``./`` stripped); absolute paths
    match only after the container mount root is stripped exactly.
    """
    results = [
        ToolResult(
            name="ruff",
            success=False,
            issues_count=4,
            issues=[
                _issue("src/main.py"),
                _issue("./src/main.py"),
                _issue("/code/src/util.py"),
                _issue("src/other.py"),
            ],
        ),
        ToolResult(name="black", success=True, issues_count=0, issues=[]),
    ]

    restricted = restrict_lint_results_to_files(
        results=results,
        changed_files=["src/main.py", "src/util.py"],
    )

    assert_that(restricted).is_length(2)
    assert_that(_kept(restricted)).is_equal_to(
        ["src/main.py", "./src/main.py", "/code/src/util.py"],
    )
    assert_that(restricted[0].issues_count).is_equal_to(3)
    assert_that(restricted[1].issues).is_empty()


@pytest.mark.parametrize(
    ("report_path", "changed", "kept"),
    [
        ("/code/lintro/x.py", "lintro/x.py", True),
        ("/code/tests/lintro/x.py", "lintro/x.py", False),
        ("/elsewhere/lintro/x.py", "lintro/x.py", False),
        ("/codebase/lintro/x.py", "lintro/x.py", False),
        ("tests/lintro/x.py", "lintro/x.py", False),
        ("x.py", "lintro/x.py", False),
        ("/code/x.py", "lintro/x.py", False),
    ],
    ids=[
        "under-root-matches",
        "under-root-nested-elsewhere-does-not",
        "outside-root-dropped",
        "root-prefix-must-be-a-path-component",
        "relative-suffix-does-not-match",
        "bare-basename-does-not-match",
        "absolute-basename-does-not-match",
    ],
)
def test_restrict_lint_results_matches_exactly_after_stripping_the_root(
    report_path: str,
    changed: str,
    kept: bool,
) -> None:
    """Only an exact repository-relative match survives; no suffix matching.

    Args:
        report_path: Path as written in the report.
        changed: The review's one changed file.
        kept: Whether the issue must survive restriction.
    """
    results = [
        ToolResult(
            name="ruff",
            success=False,
            issues_count=1,
            issues=[_issue(report_path)],
        ),
    ]

    restricted = restrict_lint_results_to_files(
        results=results,
        changed_files=[changed],
    )

    assert_that(_kept(restricted)).is_equal_to([report_path] if kept else [])


def test_restrict_lint_results_honours_a_custom_report_root() -> None:
    """The mount root is a parameter; a different container layout still works."""
    results = [
        ToolResult(
            name="ruff",
            success=False,
            issues_count=2,
            issues=[_issue("/workspace/lintro/x.py"), _issue("/code/lintro/x.py")],
        ),
    ]

    restricted = restrict_lint_results_to_files(
        results=results,
        changed_files=["lintro/x.py"],
        report_root="/workspace",
    )

    assert_that(_kept(restricted)).is_equal_to(["/workspace/lintro/x.py"])
