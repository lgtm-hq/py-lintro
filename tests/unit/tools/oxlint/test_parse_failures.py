"""Observable failure accounting for Oxlint JSON reports."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.plugins.base import ExecutionContext
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
    if failed_stage in {"verify", "both"}:
        assert_that(result.residual_unknown).is_true()
        assert_that(result.remaining_issues_count).is_none()
        assert_that(result.issues_count).is_equal_to(
            1 if failed_stage == "verify" else 0,
        )
        serialized = serialize_tool_result(result, action=Action.FIX)
        assert_that(serialized["fixed"]).is_none()
        assert_that(serialized["remaining"]).is_none()
    else:
        assert_that(result.initial_issues_count).is_none()
        assert_that(result.remaining_issues_count).is_zero()
