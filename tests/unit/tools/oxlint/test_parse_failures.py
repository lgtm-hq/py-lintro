"""Observable failure accounting for Oxlint JSON reports."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only constructs TimeoutExpired for runner tests
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

import lintro.tools.oxlint.definition as oxlint_definition
import lintro.utils.tool_executor as executor
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import ExecutionContext
from lintro.tools.core.batch_runner import BatchFixPolicy
from lintro.tools.core.verify_pass import VerifyBaseline
from lintro.tools.oxlint.definition import OxlintPlugin
from lintro.utils.json_output import serialize_tool_result


@pytest.fixture
def prepared_oxlint(
    oxlint_plugin: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> OxlintPlugin:
    """Use real runner and parser code with a prepared JavaScript target.

    Args:
        oxlint_plugin: Fresh plugin instance.
        monkeypatch: Fixture restoring preparation patches.
        tmp_path: Isolated execution directory.

    Returns:
        Plugin ready for deterministic subprocess output.
    """
    ctx = ExecutionContext(
        files=[str(tmp_path / "test.js")],
        rel_files=["test.js"],
        cwd=str(tmp_path),
        timeout=30,
    )
    monkeypatch.setattr(oxlint_plugin, "prepare", MagicMock(return_value=ctx))
    monkeypatch.setattr(
        oxlint_plugin,
        "_get_executable_command",
        MagicMock(return_value=["oxlint"]),
    )
    monkeypatch.setattr(oxlint_plugin, "_build_config_args", MagicMock(return_value=[]))
    return oxlint_plugin


@pytest.mark.parametrize("exit_success", [True, False])
@pytest.mark.parametrize(
    "report",
    [
        None,
        [],
        [{"diagnostics": []}],
        {"diagnostics": None},
        {},
        {"diagnostics": {}},
        {"diagnostics": [None]},
        {"diagnostics": [{"filename": None, "message": "Unused variable"}]},
        {"diagnostics": [{"filename": "", "message": "Unused variable"}]},
        {"diagnostics": [{"filename": "   ", "message": "Unused variable"}]},
        {"diagnostics": [{"message": "Unused variable"}]},
        {"diagnostics": [{"filename": {"path": "test.js"}, "message": "Error"}]},
        {"diagnostics": [{"filename": "test.js", "message": None}]},
        {"diagnostics": [{"filename": "test.js", "message": ""}]},
        {"diagnostics": [{"filename": "test.js", "message": " \n"}]},
        {"diagnostics": [{"filename": "test.js"}]},
    ],
)
def test_check_invalid_report_is_counted_and_fails(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    report: object,
    exit_success: bool,
) -> None:
    """Unusable reports fail even when the subprocess exits zero."""
    monkeypatch.setattr(
        prepared_oxlint,
        "_run_subprocess",
        MagicMock(return_value=(exit_success, json.dumps(report))),
    )

    result = prepared_oxlint.check(["test.js"], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues_count).is_zero()
    assert_that(list(result.issues or [])).is_empty()
    assert_that(result.output).contains("parse")
    serialized = serialize_tool_result(result, action=Action.CHECK)
    assert_that(serialized["parse_failures_count"]).is_equal_to(1)
    assert_that(serialized["success"]).is_false()


def test_check_mixed_report_keeps_findings_and_counts_each_failure(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid findings remain observable beside rejected diagnostic records."""
    report = {
        "diagnostics": [
            {"filename": "test.js", "message": "Unused variable"},
            {"filename": None, "message": "Invalid filename"},
            {"filename": "other.js", "message": None},
            None,
        ],
    }
    monkeypatch.setattr(
        prepared_oxlint,
        "_run_subprocess",
        MagicMock(return_value=(False, json.dumps(report))),
    )

    result = prepared_oxlint.check(["test.js"], {})

    assert_that(result.parse_failures_count).is_equal_to(3)
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that([issue.message for issue in result.issues or []]).is_equal_to(
        ["Unused variable"],
    )


@pytest.mark.parametrize("method", ["check", "fix"])
def test_empty_report_is_clean_after_an_invalid_run(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    """Failure accounting is per invocation and accepts valid empty reports."""
    subprocess_result = MagicMock(return_value=(True, '{"diagnostics": null}'))
    monkeypatch.setattr(prepared_oxlint, "_run_subprocess", subprocess_result)
    run = getattr(prepared_oxlint, method)
    failed = run(["test.js"], {})
    subprocess_result.return_value = (True, '{"diagnostics": []}')

    clean = run(["test.js"], {})

    assert_that(failed.success).is_false()
    assert_that(failed.parse_failures_count).is_greater_than(0)
    assert_that(clean.success).is_true()
    assert_that(clean.parse_failures_count).is_zero()
    assert_that(clean.issues_count).is_zero()
    assert_that(clean.output).is_none()


@pytest.mark.parametrize("failed_stage", ["initial", "verify", "both"])
def test_fix_parse_failures_cannot_claim_a_clean_or_fixed_result(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    failed_stage: str,
) -> None:
    """Either report in the fix pipeline can fail independently of its exit code."""
    valid = '{"diagnostics": [{"filename": "test.js", "message": "Use const"}]}'
    invalid = '{"diagnostics": null}'
    outputs = [
        (True, invalid if failed_stage in {"initial", "both"} else valid),
        (True, ""),
        (
            True,
            invalid if failed_stage in {"verify", "both"} else '{"diagnostics": []}',
        ),
    ]
    monkeypatch.setattr(
        prepared_oxlint,
        "_run_subprocess",
        MagicMock(side_effect=outputs),
    )

    result = prepared_oxlint.fix(["test.js"], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(
        2 if failed_stage == "both" else 1,
    )
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.output).contains("parse").does_not_contain(
        "All issues were successfully auto-fixed",
    )
    assert_that(result.residual_unknown).is_true()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues_count).is_equal_to(
        1 if failed_stage == "verify" else 0,
    )
    serialized = serialize_tool_result(replace(result), action=Action.FIX)
    assert_that(serialized["fixed"]).is_none()
    assert_that(serialized["remaining"]).is_none()
    if failed_stage in {"initial", "both"}:
        assert_that(result.initial_issues_count).is_none()


@pytest.mark.parametrize(
    "initial_messages",
    [[], ["Use const"], ["Earlier finding", "Use const"]],
)
def test_partial_fix_verification_keeps_new_findings_without_duplicates(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    initial_messages: list[str],
) -> None:
    """A rejected record cannot hide usable findings from the after-state."""
    initial = [
        {"filename": "test.js", "message": message} for message in initial_messages
    ]
    verified = {"filename": "test.js", "message": "Use const", "severity": "error"}
    monkeypatch.setattr(
        prepared_oxlint,
        "_run_subprocess",
        MagicMock(
            side_effect=[
                (True, json.dumps({"diagnostics": initial})),
                (True, ""),
                (False, json.dumps({"diagnostics": [verified, None]})),
            ],
        ),
    )

    result = replace(prepared_oxlint.fix(["test.js"], {}))

    expected = ["Use const"]
    if "Earlier finding" in initial_messages:
        expected.append("Earlier finding")
    assert_that([issue.message for issue in result.issues or []]).is_equal_to(expected)
    assert_that(list(result.issues or [])[0].to_display_row()["severity"]).is_equal_to(
        "ERROR",
    )
    assert_that(result.issues_count).is_equal_to(len(expected))
    assert_that(result.initial_issues_count).is_equal_to(len(initial_messages))
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.success).is_false()
    assert_that(result.residual_unknown).is_true()
    serialized = serialize_tool_result(result, action=Action.FIX)
    assert_that(serialized["fixed"]).is_none()
    assert_that(serialized["remaining"]).is_none()
    assert_that(serialized["issues"]).is_length(len(expected))


@pytest.mark.parametrize("method", ["check", "fix"])
@pytest.mark.parametrize("exit_success", [True, False])
@pytest.mark.parametrize("nonclean", [True, False])
@pytest.mark.parametrize("prefix", ["Found report:\n", "note ", "[WARN] note\n"])
def test_prose_wrapped_array_never_becomes_an_object_report(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    exit_success: bool,
    nonclean: bool,
    prefix: str,
) -> None:
    """Array envelopes fail for both exit codes and both clean and dirty inners."""
    diagnostics = [{"filename": "test.js", "message": "Use const"}] if nonclean else []
    output = f"{prefix}{json.dumps([{'diagnostics': diagnostics}])}\nDone"
    monkeypatch.setattr(
        prepared_oxlint,
        "_run_subprocess",
        MagicMock(return_value=(exit_success, output)),
    )

    result = getattr(prepared_oxlint, method)(["test.js"], {})
    serialized = serialize_tool_result(
        replace(result),
        action=Action.FIX if method == "fix" else Action.CHECK,
    )

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.parse_failures_count).is_equal_to(2 if method == "fix" else 1)
    assert_that(serialized["success"]).is_false()
    assert_that(serialized["parse_failures_count"]).is_equal_to(
        result.parse_failures_count,
    )


@pytest.mark.parametrize("method", ["check", "fix"])
@pytest.mark.parametrize("nonclean", [True, False])
@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("Warning: using default configuration\n", "\nDone"),
        ("[WARN] using default configuration\n", "\nFinished {threads: 4}"),
        ("[INFO] start\n[WARNING] config\n", " trailing {oops}"),
        ("", "\nFinished }"),
    ],
)
def test_report_tolerates_log_prefixes_and_trailing_braces(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    nonclean: bool,
    prefix: str,
    suffix: str,
) -> None:
    """Surrounding prose never invalidates a complete object report."""
    diagnostics = [{"filename": "test.js", "message": "Use const"}] if nonclean else []
    output = f"{prefix}{json.dumps({'diagnostics': diagnostics})}{suffix}"
    monkeypatch.setattr(
        prepared_oxlint,
        "_run_subprocess",
        MagicMock(return_value=(not nonclean, output)),
    )

    result = getattr(prepared_oxlint, method)(["test.js"], {})

    assert_that(result.success).is_equal_to(not nonclean)
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.issues_count).is_equal_to(int(nonclean))
    if nonclean:
        assert_that([issue.message for issue in result.issues or []]).contains(
            "Use const",
        )
    else:
        assert_that(result.output).is_none()


@pytest.mark.parametrize("verbose_label", ["Fix output", "Fix output (oxlint JSON)"])
def test_incomplete_fix_preserves_findings_summary_and_verbose_output(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    verbose_label: str,
) -> None:
    """Remove unverified fix claims while retaining useful diagnostic text."""
    diagnostic = {"filename": "test.js", "message": "Use const"}
    raw_fix = (
        "Fixed by oxlint {config}\nAll issues were successfully auto-fixed\n"
        "verbose details\n"
    )
    monkeypatch.setattr(
        oxlint_definition,
        "BatchFixPolicy",
        partial(BatchFixPolicy, verbose_output_label=verbose_label),
    )
    outputs = [
        (True, json.dumps({"diagnostics": [diagnostic, diagnostic, None]})),
        (True, raw_fix),
        (True, json.dumps({"diagnostics": [diagnostic]})),
    ]
    monkeypatch.setattr(
        prepared_oxlint,
        "_run_subprocess",
        MagicMock(side_effect=outputs),
    )

    result = prepared_oxlint.fix(["test.js"], {"verbose_fix_output": True})

    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).contains(
        "Failed to parse 1",
        "Found 1 issue(s) that cannot be auto-fixed",
        "test.js - Use const",
        f"{verbose_label}:\n{raw_fix}",
    )
    summary, separator, raw_output = (result.output or "").partition(
        f"{verbose_label}:\n",
    )
    assert_that(summary).does_not_contain(
        "Fixed 1 issue(s)",
        "All issues were successfully auto-fixed",
    )
    assert_that(separator).is_equal_to(f"{verbose_label}:\n")
    assert_that(raw_output).is_equal_to(raw_fix)


@pytest.mark.parametrize(
    "failed_stage",
    ["initial", "verify", "both", "fix-timeout", "verify-timeout", "executor"],
)
@pytest.mark.parametrize("executor_report", ["clean", "partial", "timeout"])
def test_executor_fold_preserves_unknown_fix_accounting(
    prepared_oxlint: OxlintPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_stage: str,
    executor_report: str,
) -> None:
    """Real check, fix and executor verify code must produce a valid unknown row."""
    diagnostic = {"filename": "test.js", "message": "Use const"}
    valid = json.dumps({"diagnostics": [diagnostic]})
    partial = json.dumps({"diagnostics": [diagnostic, None]})
    executor_partial = json.dumps(
        {
            "diagnostics": [
                {**diagnostic, "severity": "error"},
                {"filename": "test.js", "message": "Executor-only finding"},
                None,
            ],
        },
    )
    clean = '{"diagnostics": []}'
    timeout = subprocess.TimeoutExpired(cmd=["oxlint"], timeout=30)
    initial_failed = failed_stage in {
        "initial",
        "both",
        "fix-timeout",
        "verify-timeout",
    }
    final_failed = failed_stage in {"verify", "both"}
    outputs: list[object] = [
        (True, partial if initial_failed else valid),
        timeout if failed_stage == "fix-timeout" else (True, ""),
    ]
    if failed_stage != "fix-timeout":
        outputs.append(
            (
                timeout
                if failed_stage == "verify-timeout"
                else (True, partial if final_failed else clean)
            ),
        )
    # The executor case starts with complete fix reports; its last CHECK fails.
    if failed_stage == "executor" or executor_report == "partial":
        outputs.append((False, executor_partial))
    elif executor_report == "timeout":
        outputs.append(timeout)
    else:
        outputs.append((True, clean))
    monkeypatch.setattr(
        prepared_oxlint,
        "_run_subprocess",
        MagicMock(side_effect=outputs),
    )
    mutation = prepared_oxlint.fix(["test.js"], {})
    results = [mutation]
    monkeypatch.setattr(
        executor,
        "configure_tool_for_execution",
        MagicMock(return_value=prepared_oxlint),
    )
    ctx = MagicMock(action=Action.FIX, dry_run_preview=False, clean_stdout_output=True)

    executor._run_verify_phase(
        ctx=ctx,
        baseline=VerifyBaseline(candidates=(str(tmp_path / "test.js"),)),
        tools_to_run=["oxlint"],
        all_results=results,
        config_manager=MagicMock(),
        tool_option_dict={},
        exclude=None,
        include_venv=False,
        effective_auto_install=False,
        diff_base=None,
    )

    # replace reconstructs ToolResult and executes its invariant validation.
    result: ToolResult = replace(results[0])
    expected_failures = int(initial_failed) + int(final_failed)
    if not result.timed_out and (
        failed_stage == "executor" or executor_report == "partial"
    ):
        expected_failures += 1
    assert_that(result.parse_failures_count).is_equal_to(expected_failures)
    assert_that(result.residual_unknown).is_true()
    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    expected_messages = ["Use const"]
    if not result.timed_out and (
        failed_stage == "executor" or executor_report == "partial"
    ):
        expected_messages.append("Executor-only finding")
    assert_that(result.issues_count).is_equal_to(len(expected_messages))
    assert_that([issue.message for issue in result.issues or []]).is_equal_to(
        expected_messages,
    )
    assert_that(result.initial_issues_count).is_equal_to(None if initial_failed else 1)
    serialized = json.loads(
        json.dumps(serialize_tool_result(result, action=Action.FIX)),
    )
    assert_that(serialized["parse_failures_count"]).is_equal_to(expected_failures)
    assert_that(serialized["fixed"]).is_none()
    assert_that(serialized["net_resolved"]).is_none()
    assert_that(serialized["remaining"]).is_none()
    assert_that(serialized["residual_unknown"]).is_true()
    assert_that(serialized["timed_out"]).is_equal_to("timeout" in failed_stage)
