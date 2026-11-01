"""Post-fix validation must distinguish an unanswered check from a clean one."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion
from lintro.ai.validation import validate_applied_fixes, verify_fixes
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from tests.unit.tool_result_helpers import legacy_result

from .conftest import MockIssue


@pytest.fixture
def suggestions(sample_issues: list[MockIssue]) -> list[AIFixSuggestion]:
    """Build multiple fixes so an unanswered tool must account for all of them."""
    return [
        AIFixSuggestion(
            file=issue.file,
            line=issue.line,
            code=issue.code,
            tool_name="ruff",
            original_code="assert x",
            suggested_code="if not x: raise ValueError",
            explanation="Replace assert",
        )
        for issue in sample_issues
    ]


@pytest.fixture(params=["timed_out", "skipped", "no_files", "execution_failure"])
def unmeasured_rerun(request: pytest.FixtureRequest) -> ToolResult:
    """Return each unmeasured shape, including successful skips."""
    return ToolResult(
        name="ruff",
        success=request.param in {"no_files", "skipped"},
        timed_out=request.param == "timed_out",
        skipped=request.param == "skipped",
        skip_reason="Unavailable version" if request.param == "skipped" else None,
        no_files=request.param == "no_files",
        output="No post-fix verdict",
        inspected_files=(
            False
            if request.param == "skipped"
            or request.param == "no_files"
            or request.param == "timed_out"
            else None
        ),
    )


@pytest.mark.parametrize("empty_issues", [None, []], ids=["none", "empty"])
def test_standalone_unmeasured_rerun_cannot_verify(
    suggestions: list[AIFixSuggestion],
    unmeasured_rerun: ToolResult,
    empty_issues: Sequence[BaseIssue] | None,
) -> None:
    """Both None and an empty failed result leave every suggestion unverified."""
    # Deliberately inject invalid None to keep the fail-closed regression case.
    unmeasured_rerun = legacy_result(
        **(vars(unmeasured_rerun) | {"issues": empty_issues}),
    )
    tool = MagicMock()
    tool.check.return_value = unmeasured_rerun
    with patch("lintro.tools.tool_manager.get_tool", return_value=tool):
        result = validate_applied_fixes(suggestions)

    assert_that(result).is_not_none()
    if result is None:
        pytest.fail("An unmeasured rerun must report unverified suggestions")
    assert_that(result.verified).is_equal_to(0)
    assert_that(result.unverified).is_equal_to(len(suggestions))
    assert_that(result.unverified_by_tool).is_equal_to({"ruff": len(suggestions)})
    assert_that(result.new_issues).is_equal_to(0)
    assert_that(result.details).is_length(len(suggestions))
    assert_that(result.details[0]).contains("unmeasured")


@pytest.mark.parametrize("empty_issues", [None, []], ids=["none", "empty"])
@pytest.mark.parametrize("original_flag", ["timed_out", "skipped", "no_files", None])
def test_unified_unmeasured_rerun_preserves_original_result(
    suggestions: list[AIFixSuggestion],
    sample_issues: list[MockIssue],
    unmeasured_rerun: ToolResult,
    empty_issues: Sequence[BaseIssue] | None,
    original_flag: str | None,
) -> None:
    """Unanswered checks cannot rewrite counts, findings, output or failure flags."""
    # Deliberately inject invalid None to keep the fail-closed regression case.
    unmeasured_rerun = legacy_result(
        **(vars(unmeasured_rerun) | {"issues": empty_issues}),
    )
    original = legacy_result(
        name="ruff",
        success=False,
        issues=sample_issues,
        issues_count=3,
        initial_issues_count=5,
        fixed_issues_count=2,
        remaining_issues_count=3,
        timed_out=original_flag == "timed_out",
        skipped=original_flag == "skipped",
        skip_reason="Original skip" if original_flag == "skipped" else None,
        no_files=original_flag == "no_files",
        output="Original findings",
        inspected_files=(
            False
            if original_flag == "skipped"
            or original_flag == "no_files"
            or original_flag == "timed_out"
            else None
        ),
    )
    before = deepcopy(original)
    tool = MagicMock()
    tool.check.return_value = unmeasured_rerun
    with patch("lintro.tools.tool_manager.get_tool", return_value=tool):
        result = verify_fixes(
            applied_suggestions=suggestions,
            by_tool={"ruff": (original, list(sample_issues))},
        )

    assert_that(result).is_not_none()
    if result is None:
        pytest.fail("An unmeasured rerun must return a validation result")
    assert_that((result.verified, result.unverified)).is_equal_to((0, 3))
    assert_that(result.verified_by_tool).is_empty()
    assert_that(result.unverified_by_tool).is_equal_to({"ruff": 3})
    assert_that(original.remaining_issues_count).is_equal_to(3)
    assert_that(original).is_equal_to(before)


@pytest.mark.parametrize("path", ["standalone", "unified"])
@pytest.mark.parametrize(
    "status",
    ["clean", "findings", "timed_out", "skipped", "no_files"],
)
def test_post_fix_measurement_handles_real_findings(
    path: str,
    status: str,
    suggestions: list[AIFixSuggestion],
    sample_issues: list[MockIssue],
) -> None:
    """Only completed checks can match findings or establish that fixes worked."""
    remaining = [sample_issues[0], MockIssue(file="src/new.py", code="NEW", line=1)]
    rerun = legacy_result(
        name="ruff",
        success=status in {"clean", "skipped", "no_files"},
        issues=[] if status == "clean" else remaining,
        issues_count=0 if status == "clean" else len(remaining),
        timed_out=status == "timed_out",
        skipped=status == "skipped",
        skip_reason="Version gate" if status == "skipped" else None,
        no_files=status == "no_files",
        inspected_files=(
            False
            if status == "skipped" or status == "no_files" or status == "timed_out"
            else None
        ),
    )
    original = ToolResult(
        name="ruff",
        success=False,
        issues=sample_issues,
        issues_count=3,
        remaining_issues_count=3,
    )
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]] = {
        "ruff": (original, list(sample_issues)),
    }
    tool = MagicMock()
    tool.check.return_value = rerun
    with patch("lintro.tools.tool_manager.get_tool", return_value=tool):
        if path == "standalone":
            result = validate_applied_fixes(suggestions)
        else:
            result = verify_fixes(
                applied_suggestions=suggestions,
                by_tool=by_tool,
            )

    assert_that(result).is_not_none()
    if result is None:
        pytest.fail("The tool returned a post-fix check")
    expected = {"clean": (3, 0, 0), "findings": (2, 1, 1)}.get(status, (0, 3, 0))
    assert_that((result.verified, result.unverified, result.new_issues)).is_equal_to(
        expected,
    )
    if path == "unified":
        updated = by_tool["ruff"][0]
        expected_remaining = {"clean": 0, "findings": 2}.get(status, 3)
        assert_that(updated.remaining_issues_count).is_equal_to(expected_remaining)
        assert_that(updated.success).is_equal_to(status == "clean")
        assert_that(original.remaining_issues_count).is_equal_to(3)
