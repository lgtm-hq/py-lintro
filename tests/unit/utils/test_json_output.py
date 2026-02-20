"""Tests for JSON output AI metadata serialization."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.json_output import create_json_output


class TestCreateJsonOutput:
    """Tests for create_json_output."""

    def test_includes_summary_and_fix_suggestions_together(self):
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

    def test_normalizes_legacy_suggestions_key(self):
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
