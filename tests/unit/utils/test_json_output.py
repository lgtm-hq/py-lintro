"""Tests for JSON output AI metadata serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.json_output import create_json_output, serialize_tool_result
from lintro.utils.output.file_writer import write_output_file


@dataclass
class _StubIssue(BaseIssue):
    """Minimal issue used to exercise JSON serialization."""

    file: str = "src/main.py"
    line: int = 3
    code: str = "E001"
    message: str = "boom"


@pytest.fixture
def check_result_with_issue() -> ToolResult:
    """Return a check-mode ToolResult carrying a single issue."""
    return ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        output="ruff raw output",
        issues=[_StubIssue()],
        parse_failures_count=0,
    )


@pytest.fixture
def fix_result_with_split() -> ToolResult:
    """Return a fix-mode ToolResult with pre-fix and remaining issues."""
    detected = _StubIssue()
    remaining = _StubIssue(code="E002", message="still here")
    return ToolResult(
        name="prettier",
        success=False,
        issues_count=1,
        output="prettier raw output",
        issues=[remaining],
        initial_issues=[detected, remaining],
        initial_issues_count=2,
        fixed_issues_count=1,
        remaining_issues_count=1,
        parse_failures_count=0,
    )


def test_serialize_tool_result_fix_mode_defaults_unset_counts_to_zero() -> None:
    """FIX serialization emits integers when split counts are unset."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        output="ruff raw output",
        issues=[_StubIssue()],
    )

    data = serialize_tool_result(result, action=Action.FIX)

    assert_that(data["fixed"]).is_equal_to(0)
    assert_that(data["remaining"]).is_equal_to(0)


def test_serialize_tool_result_includes_output_and_issues(
    check_result_with_issue: ToolResult,
) -> None:
    """The shared serializer includes raw output and the issues array."""
    data = serialize_tool_result(check_result_with_issue, action=Action.CHECK)

    assert_that(data).contains_key("output")
    assert_that(data["output"]).is_equal_to("ruff raw output")
    assert_that(data["issues_count"]).is_equal_to(1)
    assert_that(data).contains_key("issues")
    assert_that(data["issues"]).is_length(1)
    assert_that(data["parse_failures_count"]).is_equal_to(0)


def test_file_and_stdout_serializers_produce_same_per_tool_object(
    tmp_path: Path,
    check_result_with_issue: ToolResult,
) -> None:
    """Both serialization paths emit an identical per-tool object (check)."""
    stdout_payload = create_json_output(
        action=Action.CHECK,
        results=[check_result_with_issue],
        total_issues=1,
        total_fixed=0,
        total_remaining=1,
        exit_code=1,
    )
    stdout_result = stdout_payload["results"][0]

    output_path = tmp_path / "report.json"
    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.JSON,
        all_results=[check_result_with_issue],
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )
    file_result = json.loads(output_path.read_text())["results"][0]

    assert_that(file_result).is_equal_to(stdout_result)
    assert_that(stdout_result).contains_key("output")
    assert_that(stdout_result).contains_key("issues")


def test_file_and_stdout_serializers_parity_fix_mode(
    tmp_path: Path,
    fix_result_with_split: ToolResult,
) -> None:
    """Both serialization paths agree in fix mode, including merged counts."""
    stdout_payload = create_json_output(
        action=Action.FIX,
        results=[fix_result_with_split],
        total_issues=2,
        total_fixed=1,
        total_remaining=1,
        exit_code=1,
    )
    stdout_result = stdout_payload["results"][0]

    output_path = tmp_path / "report.json"
    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.JSON,
        all_results=[fix_result_with_split],
        action=Action.FIX,
        total_issues=2,
        total_fixed=1,
    )
    file_result = json.loads(output_path.read_text())["results"][0]

    assert_that(file_result).is_equal_to(stdout_result)
    # Merged/deduped count: detected [E001, E002] + remaining [E002] -> 2
    assert_that(stdout_result["issues_count"]).is_equal_to(2)
    assert_that(stdout_result["fixed"]).is_equal_to(1)
    assert_that(stdout_result["remaining"]).is_equal_to(1)


def test_check_mode_total_remaining_mirrors_total_issues(
    check_result_with_issue: ToolResult,
) -> None:
    """Check-mode summary mirrors total_issues instead of a constant 0."""
    data = create_json_output(
        action=Action.CHECK,
        results=[check_result_with_issue],
        total_issues=5,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    assert_that(data["summary"]["total_remaining"]).is_equal_to(5)
    assert_that(data["summary"]["total_issues"]).is_equal_to(5)


def test_create_json_output_includes_summary_and_fix_suggestions_together() -> None:
    """Verify JSON output includes both AI summary and fix suggestions."""
    result = ToolResult(name="ruff", success=False, issues_count=1)
    result.ai_metadata = {
        "summary": {
            "overview": "Summary text",
            "key_patterns": [],
            "priority_actions": [],
            "triage_suggestions": [],
            "estimated_effort": "10 minutes",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_estimate": 0.001,
        },
        "fix_suggestions": [
            {
                "file": "src/main.py",
                "line": 1,
                "code": "B101",
                "explanation": "Replace assert",
                "confidence": "high",
                "diff": "--- a/src/main.py",
                "input_tokens": 5,
                "output_tokens": 4,
                "cost_estimate": 0.001,
            },
        ],
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=1,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    assert_that(data).contains_key("ai_summary")
    assert_that(data["ai_summary"]["overview"]).is_equal_to("Summary text")
    assert_that(data["results"][0]["ai_metadata"]).contains_key("summary")
    assert_that(data["results"][0]["ai_metadata"]).contains_key(
        "fix_suggestions",
    )


def test_create_json_output_normalizes_legacy_suggestions_key() -> None:
    """Test that legacy suggestions key is normalized correctly."""
    result = ToolResult(name="ruff", success=False, issues_count=1)
    result.ai_metadata = {
        "summary": {"overview": "Legacy"},
        "suggestions": [{"file": "a.py", "line": 1}],
        "type": "fix_suggestions",
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=1,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    assert_that(data["results"][0]["ai_metadata"]).contains_key(
        "fix_suggestions",
    )
    assert_that(data["results"][0]["ai_metadata"]).does_not_contain_key(
        "suggestions",
    )


def test_create_json_output_includes_ai_count_fields() -> None:
    """Verify AI count fields are serialized into JSON output."""
    result = ToolResult(name="ruff", success=False, issues_count=3)
    result.ai_metadata = {
        "fixed_count": 2,
        "verified_count": 1,
        "unverified_count": 1,
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=3,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    ai_meta = data["results"][0]["ai_metadata"]
    assert_that(ai_meta["fixed_count"]).is_equal_to(2)
    assert_that(ai_meta["applied_count"]).is_equal_to(2)
    assert_that(ai_meta["verified_count"]).is_equal_to(1)
    assert_that(ai_meta["unverified_count"]).is_equal_to(1)


def test_create_json_output_includes_ai_metrics() -> None:
    """Verify AI telemetry metrics are preserved through normalization."""
    result = ToolResult(name="ruff", success=False, issues_count=1)
    result.ai_metadata = {
        "ai_metrics": {
            "total_api_calls": 5,
            "total_input_tokens": 1000,
            "total_output_tokens": 500,
            "total_cost_usd": 0.01,
        },
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=1,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    ai_meta = data["results"][0]["ai_metadata"]
    assert_that(ai_meta).contains_key("ai_metrics")
    assert_that(ai_meta["ai_metrics"]["total_api_calls"]).is_equal_to(5)
    assert_that(ai_meta["ai_metrics"]["total_cost_usd"]).is_equal_to(0.01)


def test_create_json_output_counts_survive_legacy_normalization() -> None:
    """Verify counts and metrics survive alongside legacy key normalization."""
    result = ToolResult(name="ruff", success=False, issues_count=1)
    result.ai_metadata = {
        "summary": {"overview": "Legacy with counts"},
        "suggestions": [{"file": "a.py", "line": 1}],
        "type": "fix_suggestions",
        "fixed_count": 1,
        "verified_count": 1,
        "unverified_count": 0,
        "ai_metrics": {
            "total_api_calls": 2,
            "total_cost_usd": 0.005,
        },
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=1,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    ai_meta = data["results"][0]["ai_metadata"]
    assert_that(ai_meta).contains_key("fix_suggestions")
    assert_that(ai_meta).does_not_contain_key("suggestions")
    assert_that(ai_meta["fixed_count"]).is_equal_to(1)
    assert_that(ai_meta["verified_count"]).is_equal_to(1)
    assert_that(ai_meta["unverified_count"]).is_equal_to(0)
    assert_that(ai_meta).contains_key("ai_metrics")
    assert_that(ai_meta["ai_metrics"]["total_api_calls"]).is_equal_to(2)
