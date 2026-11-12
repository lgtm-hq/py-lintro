"""Stylelint runs without a complete verdict are execution failures (#3352).

Stylelint 18 prints a one-line JSON array on every completed run, with empty
``warnings`` when clean, and exits 2 on violations. A broken config file makes
it print a stack trace and exit 1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.stylelint.definition import StylelintPlugin
from tests.unit.tools.stylelint.conftest import make_ctx

FINDING = (
    '{"source":"/repo/a.css","warnings":[{"line":1,"column":12,'
    '"rule":"color-hex-length","severity":"error",'
    '"text":"Expected \\"#ffffff\\" to be \\"#fff\\" (color-hex-length)"}]}'
)
REPORT = f"[{FINDING}]"
CLEAN = '[{"source":"/repo/a.css","warnings":[]}]'
MALFORMED = f'[{FINDING},{{"source":"/repo/b.css","warnings":"oops"}}]'
CONFIG_CRASH = (
    "SyntaxError: JSON Error in /repo/.stylelintrc.json:\n"
    "Unexpected end of JSON input\n"
    "    at JSON.parse (<anonymous>)\n"
    "    at loadJson (/x/cosmiconfig/dist/loaders.js:37:21)\n"
)


def _run(
    plugin: StylelintPlugin,
    tmp_path: Path,
    runs: list[tuple[bool, str]],
    *,
    fix: bool = False,
) -> tuple[ToolResult, int]:
    """Run ``check`` or ``fix`` with canned Stylelint invocations.

    Args:
        plugin: The plugin under test.
        tmp_path: Working directory for the context.
        runs: ``(exit 0?, combined output)`` for each invocation, in order.
        fix: Run ``fix`` instead of ``check``.

    Returns:
        The result and how many Stylelint invocations ran.
    """
    with (
        patch.object(plugin, "prepare", return_value=make_ctx(tmp_path, ["a.css"])),
        patch.object(plugin, "_get_executable_command", return_value=["stylelint"]),
        patch.object(plugin, "_run_subprocess", side_effect=runs) as run,
    ):
        method = plugin.fix if fix else plugin.check
        result = method([str(tmp_path / "a.css")], {})
    return result, run.call_count


def test_check_empty_exit_zero_output_is_no_report(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """No output at exit 0 is not a clean, inspected pass."""
    result, _ = _run(stylelint_plugin, tmp_path, [(True, "")])

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).is_equal_to("Stylelint printed no report.")


def test_check_config_crash_is_named_not_counted_as_parse_failure(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """A crash quotes Stylelint's own first lines, not the whole trace."""
    result, _ = _run(stylelint_plugin, tmp_path, [(False, CONFIG_CRASH)])

    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).is_equal_to(
        "Stylelint exited with an error and reported no findings: "
        "SyntaxError: JSON Error in /repo/.stylelintrc.json: | "
        "Unexpected end of JSON input | at JSON.parse (<anonymous>)",
    )


def test_check_malformed_report_keeps_findings_without_inspection(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """A malformed source entry fails the run and keeps the real warning."""
    result, _ = _run(stylelint_plugin, tmp_path, [(False, MALFORMED)])

    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues).extracting("code").is_equal_to(["color-hex-length"])
    assert_that(result.output).contains("could not be fully parsed").does_not_contain(
        "oops",
    )


@pytest.mark.parametrize(
    ("run", "issues"),
    [((True, CLEAN), 0), ((False, REPORT), 1)],
    ids=["clean", "findings"],
)
def test_check_complete_report_still_claims_inspection(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
    run: tuple[bool, str],
    issues: int,
) -> None:
    """Complete reports, clean or with findings, remain inspected verdicts."""
    result, _ = _run(stylelint_plugin, tmp_path, [run])

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(issues)


def test_fix_stops_when_the_initial_check_prints_no_report(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """An empty initial check is not "nothing to fix": --fix never runs."""
    result, calls = _run(stylelint_plugin, tmp_path, [(True, "")], fix=True)

    assert_that(calls).is_equal_to(1)
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.output).is_equal_to("Stylelint printed no report.")


def test_fix_recheck_without_a_report_cannot_read_as_all_fixed(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """An empty re-check keeps every initial finding as remaining."""
    result, _ = _run(
        stylelint_plugin,
        tmp_path,
        [(False, REPORT), (True, CLEAN), (True, "")],
        fix=True,
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.fixed_issues_count).is_zero()
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.output).is_equal_to("Stylelint re-check printed no report.")


def test_fix_malformed_recheck_names_the_reason_without_inspection(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """A malformed re-check report is named, not dumped, and proves no coverage."""
    result, _ = _run(
        stylelint_plugin,
        tmp_path,
        [(False, REPORT), (True, CLEAN), (False, MALFORMED)],
        fix=True,
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).is_equal_to(
        "Stylelint printed a report that could not be fully parsed "
        "(1 malformed record(s) or container(s)); its findings are incomplete.",
    )
