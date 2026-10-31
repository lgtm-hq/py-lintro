"""Ruff fix reports must retain rejected-record evidence at every stage."""

from __future__ import annotations

import subprocess  # nosec B404 - constructs process results and timeout exceptions only
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.tools.ruff.definition import RuffPlugin


_SYNTAX_ERROR = '{"file": "syntax.py", "message": "Expected expression", "code": null}'
_MIXED_REPORT = f"[{{}}, {_SYNTAX_ERROR}]"


@pytest.mark.parametrize("stage", ["initial", "final", "both", "lint-disabled"])
@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize(
    ("report", "failures"),
    [
        ("[{}]", 1),
        ('[{"filename": "a.py"}]', 1),
        ("[null, 42]", 2),
        ("{}", 1),
        ('{"filename":', 1),
    ],
)
def test_fix_rejects_incomplete_diagnostics(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    stage: str,
    returncode: int,
    report: str,
    failures: int,
) -> None:
    """Real discovery, commands and parsers fail closed on rejected records."""
    initial = report if stage != "final" else "[]"
    final = report if stage in ("final", "both") else "[]"
    reports = [initial, final] if stage != "lint-disabled" else [initial]
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch(
            "subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(["ruff"], returncode, output, "")
                for output in reports
            ],
        ),
    ):
        result = ruff_plugin.fix(
            [temp_python_file],
            {"format": False, "lint_fix": stage != "lint-disabled"},
        )

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(
        failures * (2 if stage == "both" else 1),
    )
    assert_that(result.issues).is_empty()
    assert_that(
        (
            result.initial_issues_count,
            result.fixed_issues_count,
            result.remaining_issues_count,
            result.issues_count,
        ),
    ).is_equal_to((0, 0, 0, 0))


@pytest.mark.parametrize("stage", ["format-check", "lint-fix", "unsafe", "format"])
def test_fix_retains_parse_failures_across_timeouts(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    stage: str,
) -> None:
    """Later timeouts retain usable null-code issues and all earlier rejections."""
    reports: list[tuple[bool, str] | subprocess.TimeoutExpired] = [
        (False, _MIXED_REPORT),
    ]
    if stage == "format":
        reports.append((True, ""))  # Initial format check
    if stage in ("unsafe", "format"):
        reports.append((False, _MIXED_REPORT))  # Lint fix
    if stage == "format":
        reports.append((True, "[{}]"))  # Unsafe probe
    reports.append(subprocess.TimeoutExpired(cmd=["ruff"], timeout=30))
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(ruff_plugin, "_run_subprocess", side_effect=reports),
    ):
        result = ruff_plugin.fix(
            [temp_python_file],
            {"format": stage in ("format-check", "format"), "unsafe_fixes": False},
        )

    expected_failures = {"format-check": 1, "lint-fix": 1, "unsafe": 2, "format": 3}
    assert_that(result.parse_failures_count).is_equal_to(expected_failures[stage])
    assert_that(result.success).is_false()
    # The unsafe probe's timeout is optional; its earlier parse evidence is not.
    assert_that(result.timed_out).is_equal_to(stage != "unsafe")
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).extracting("code").is_equal_to([""])


@pytest.mark.parametrize("report", [_MIXED_REPORT, f'{{"filename":\n{_SYNTAX_ERROR}'])
def test_fix_accumulates_unsafe_probe_rejections(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    report: str,
) -> None:
    """An unusable unsafe report cannot erase the remaining syntax error."""
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            ruff_plugin,
            "_run_subprocess",
            side_effect=[
                (False, report),
                (False, report),
                (True, "[{}, null]"),
            ],
        ),
    ):
        result = ruff_plugin.fix(
            [temp_python_file],
            {"format": False, "unsafe_fixes": False},
        )

    assert_that(result.parse_failures_count).is_equal_to(4)
    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.issues).extracting("message").is_equal_to(
        ["Expected expression"],
    )


@pytest.mark.parametrize("fixed", [True, False])
def test_fix_accepts_null_code_diagnostics(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    fixed: bool,
) -> None:
    """A null rule code remains a usable diagnostic in both fix measurements."""
    report = f"[{_SYNTAX_ERROR}]"
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            ruff_plugin,
            "_run_subprocess",
            side_effect=[(False, report), (fixed, "[]" if fixed else report)],
        ),
    ):
        result = ruff_plugin.fix(
            [temp_python_file],
            {"format": False, "unsafe_fixes": True},
        )

    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.success).is_equal_to(fixed)
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert result.initial_issues is not None
    assert_that(result.initial_issues[0].get_code()).is_empty()
    assert_that(result.remaining_issues_count).is_equal_to(int(not fixed))
    assert_that(result.fixed_issues_count).is_equal_to(int(fixed))


@pytest.mark.parametrize("output", ["", "[]"])
def test_fix_empty_reports_stay_clean(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    output: str,
) -> None:
    """Valid empty reports remain successful through the same real fix path."""
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(ruff_plugin, "_run_subprocess", return_value=(True, output)),
    ):
        result = ruff_plugin.fix([temp_python_file], {"format": False})

    assert_that(result.success).is_true()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.issues_count).is_zero()
