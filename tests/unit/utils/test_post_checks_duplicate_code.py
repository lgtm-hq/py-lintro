"""Wiring tests for the duplicate-code gate inside post-checks (issue #2293)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.pylint.pylint_issue import PylintIssue
from lintro.utils import post_checks
from lintro.utils.duplicate_code import PYLINT_ANALYSED_METADATA_KEY


@dataclass
class _RecordingLogger:
    """Minimal console logger capturing the gate's output lines.

    Attributes:
        lines: Text of every console line the gate emitted.
    """

    lines: list[str] = field(default_factory=list)

    def console_output(self, text: str, **_kwargs: Any) -> None:
        """Record a console line.

        Args:
            text: The line the gate wrote.
            **_kwargs: Styling arguments, ignored.
        """
        self.lines.append(text)


def _duplicate_result(count: int) -> ToolResult:
    """Build a pylint result carrying ``count`` duplicate-code findings.

    Args:
        count: Number of R0801 findings to include.

    Returns:
        ToolResult: A failing pylint result.
    """
    issues = [
        PylintIssue(
            file=f"lintro/tools/definitions/tool_{index}.py",
            line=1,
            code="R0801",
            symbol="duplicate-code",
            message_type="refactor",
            message="Similar lines in 2 files",
        )
        for index in range(count)
    ]
    return ToolResult(
        name="pylint",
        success=False,
        issues_count=count,
        issues=issues,
        metadata={PYLINT_ANALYSED_METADATA_KEY: True},
    )


@pytest.fixture
def baseline_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the gate at a baseline of two clone sets.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        post_checks,
        "load_lintro_tool_config",
        lambda _name: {"duplicate_code_baseline": 2},
    )


def test_gate_clears_baselined_findings_from_the_totals(
    baseline_config: None,
) -> None:
    """At the baseline the run is green and pylint reports no issues.

    Args:
        baseline_config: Fixture configuring a baseline of two.
    """
    logger = _RecordingLogger()
    results = [_duplicate_result(2)]

    total = post_checks._run_duplicate_code_gate(
        all_results=results,
        total_issues=2,
        json_output_mode=False,
        logger=logger,  # type: ignore[arg-type]
    )

    assert_that(total).is_equal_to(0)
    assert_that(results).is_length(1)
    assert_that(results[0].success).is_true()
    assert_that(logger.lines).contains("duplicate-code count 2 is within baseline 2")


def test_gate_fails_the_run_above_the_baseline(baseline_config: None) -> None:
    """One clone set more than the baseline appends a failing result.

    Args:
        baseline_config: Fixture configuring a baseline of two.
    """
    logger = _RecordingLogger()
    results = [_duplicate_result(3)]

    total = post_checks._run_duplicate_code_gate(
        all_results=results,
        total_issues=3,
        json_output_mode=False,
        logger=logger,  # type: ignore[arg-type]
    )

    assert_that(total).is_equal_to(1)
    assert_that(results).is_length(2)
    gate_result = results[-1]
    assert_that(gate_result.name).is_equal_to("duplicate-code")
    assert_that(gate_result.success).is_false()
    assert_that(gate_result.output).is_equal_to(
        "duplicate-code count 3 exceeds baseline 2; baseline may only shrink",
    )


def test_gate_is_inert_without_a_configured_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured baseline leaves the run untouched.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(post_checks, "load_lintro_tool_config", lambda _name: {})
    logger = _RecordingLogger()
    results = [_duplicate_result(3)]

    total = post_checks._run_duplicate_code_gate(
        all_results=results,
        total_issues=3,
        json_output_mode=False,
        logger=logger,  # type: ignore[arg-type]
    )

    assert_that(total).is_equal_to(3)
    assert_that(results).is_length(1)
    assert_that(results[0].issues_count).is_equal_to(3)
    assert_that(logger.lines).is_empty()


def test_gate_stays_quiet_in_json_mode(baseline_config: None) -> None:
    """JSON output carries the verdict in the results, not on the console.

    Args:
        baseline_config: Fixture configuring a baseline of two.
    """
    logger = _RecordingLogger()
    results = [_duplicate_result(2)]

    post_checks._run_duplicate_code_gate(
        all_results=results,
        total_issues=2,
        json_output_mode=True,
        logger=logger,  # type: ignore[arg-type]
    )

    assert_that(logger.lines).is_empty()


def test_execute_post_checks_applies_the_gate_end_to_end(
    baseline_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public post-check entry point returns the gated totals.

    Args:
        baseline_config: Fixture configuring a baseline of two.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(post_checks, "load_post_checks_config", lambda: {})
    monkeypatch.setattr(post_checks, "load_module_size_config", lambda: {})
    logger = _RecordingLogger()
    results = [_duplicate_result(3)]

    total_issues, _total_fixed, _total_remaining = post_checks.execute_post_checks(
        action=Action.CHECK,
        paths=["lintro/utils"],
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="grid",
        verbose=False,
        raw_output=False,
        logger=logger,  # type: ignore[arg-type]
        all_results=results,
        total_issues=3,
        total_fixed=0,
        total_remaining=0,
    )

    assert_that(total_issues).is_equal_to(1)
    assert_that(results[-1].name).is_equal_to("duplicate-code")
    assert_that(results[-1].success).is_false()
