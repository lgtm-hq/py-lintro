"""An advisory tool lintro refused to run is an error, not a result (#3497).

The advisory text output renders only the issue table, so a refusal beside
one real finding would be unmentioned, and ``--advisory-only`` would exit 0.
It now names the reason, keeps the findings, and exits 2 after rendering.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner, Result

import lintro.utils.execution.advisory as advisory_module
from lintro.ai.review.error_contract import REVIEW_ERROR_EXIT_CODE
from lintro.cli import cli
from lintro.config.lintro_config import LintroConfig
from lintro.models.core.tool_outcome import ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools import tool_manager
from tests.refused_plugin import FINDING_MESSAGE, RefusingPlugin, copy_configured


def _run(monkeypatch: pytest.MonkeyPatch, plugin: RefusingPlugin) -> list[ToolResult]:
    """Run one plugin through the advisory runner ``lintro review`` uses.

    Args:
        monkeypatch: Pytest patch fixture.
        plugin: The plugin to run.

    Returns:
        The runner's results.
    """
    monkeypatch.setattr(advisory_module, "UnifiedConfigManager", lambda: object())
    monkeypatch.setattr(tool_manager, "get_tool", {plugin.tool_name: plugin}.get)
    monkeypatch.setattr(
        advisory_module,
        "configure_tool_for_execution",
        copy_configured,
    )
    return advisory_module.run_advisory_tools(
        paths=["src"],
        tool_names=[plugin.tool_name],
        lintro_config=LintroConfig(),
    )


@pytest.mark.parametrize("with_finding", [False, True], ids=["no-findings", "finding"])
def test_refused_advisory_tool_is_errored_shown_and_flagged_in_json(
    monkeypatch: pytest.MonkeyPatch,
    with_finding: bool,
) -> None:
    """A refusal is named in the text, counts as errored, and is in the JSON.

    Args:
        monkeypatch: Pytest patch fixture.
        with_finding: Whether the refused tool also reported a finding.
    """
    results = _run(monkeypatch, RefusingPlugin(with_finding=with_finding))

    text = advisory_module.render_advisory_results(results=results)
    payload = advisory_module.advisory_results_to_payload(results)

    assert_that(advisory_module.advisory_tools_errored(results)).is_true()
    assert_that(text).contains(
        "Advisory: tsc\n  did not run / incomplete: lintro refused to run tsc: "
        "Refusing to pass argument 1",
    )
    assert_that(FINDING_MESSAGE in text).is_equal_to(with_finding)
    assert_that(payload[0]).contains_entry(
        {"execution_failed": True},
        {"findings_complete": False},
        {"issues_count": int(with_finding)},
    )


def test_completed_advisory_tool_reports_complete_fields_and_no_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool whose command ran gets no note and is not errored.

    Args:
        monkeypatch: Pytest patch fixture.
    """
    results = _run(
        monkeypatch,
        RefusingPlugin(command=(sys.executable, "-c", "pass")),
    )

    payload = advisory_module.advisory_results_to_payload(results)

    assert_that(advisory_module.advisory_tools_errored(results)).is_false()
    text = advisory_module.render_advisory_results(results=results)
    assert_that(text).does_not_contain("did not run")
    assert_that(payload[0]).contains_entry({"execution_failed": False})
    assert_that(payload[0]).contains_key(
        "tool",
        "success",
        "output",
        "skipped",
        "skip_reason",
        "issues_count",
        "issues",
        "findings_complete",
    )


def _advisory_only(results: list[ToolResult], *, output: str) -> Result:
    """Run ``lintro review --advisory-only`` over fixed advisory results.

    Args:
        results: What the advisory tools return.
        output: ``terminal`` or ``json``.

    Returns:
        The CLI result.
    """
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=results,
        ),
    ):
        return CliRunner().invoke(
            cli,
            ["review", "--advisory-only", "--output", output],
        )


@pytest.mark.parametrize("output", ["terminal", "json"])
def test_advisory_only_shows_an_unfinished_tools_findings_then_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    """Real findings of a refused tool are rendered before the run exits 2.

    Args:
        monkeypatch: Pytest patch fixture.
        output: ``--output`` value.
    """
    results = _run(monkeypatch, RefusingPlugin(with_finding=True))

    outcome = _advisory_only(results, output=output)

    assert_that(outcome.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    assert_that(outcome.output).contains(FINDING_MESSAGE)
    assert_that(outcome.output).does_not_contain("unexpected error")
    if output == "json":
        entry = json.loads(outcome.output)["advisory"][0]
        assert_that(entry).contains_entry(
            {"incomplete": True},
            {"execution_failed": True},
            {"issues_count": 1},
        )
        assert_that(entry["output"]).starts_with("lintro refused to run tsc: ")
    else:
        assert_that(outcome.output).contains(
            "did not run / incomplete: lintro refused to run tsc: ",
        )


def test_advisory_only_names_a_partial_parse_without_quoting_a_finding() -> None:
    """A partial parse fails closed with its own reason, findings shown."""
    partial = ToolResult(
        name="idiom-review",
        success=False,
        issues_count=1,
        issues=[
            BaseIssue(file="src/a.py", line=3, column=1, message="line too long"),
        ],
        parse_failures_count=2,
        output="src/a.py:3:1 E501 line too long\n{garbled",
    )

    outcome = _advisory_only([partial], output="terminal")

    assert_that(outcome.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    assert_that(outcome.output).contains(
        "did not run / incomplete: 2 part(s) of idiom-review's output could "
        "not be parsed; its findings are partial",
    )
    assert_that(outcome.output).does_not_contain("incomplete: src/a.py")
    assert_that(outcome.output).does_not_contain("unexpected error")
    assert_that(outcome.output).contains("line too long")


def test_unavailable_advisory_tool_is_a_skip_with_its_reason() -> None:
    """An unavailable tool is named with its reason and does not fail the run."""
    unavailable = ToolResult.from_outcome(
        ToolOutcome(execution=Unavailable(reason="idiom-review binary missing")),
        name="idiom-review",
        output="idiom-review unavailable",
    )

    terminal = _advisory_only([unavailable], output="terminal")
    entry = json.loads(_advisory_only([unavailable], output="json").output)

    assert_that(terminal.exit_code).is_equal_to(0)
    assert_that(terminal.output).contains(
        "Advisory: idiom-review\n  unavailable: idiom-review binary missing",
    )
    assert_that(entry["advisory"][0]).contains_entry({"incomplete": False})


def test_clean_legacy_advisory_tool_is_not_incomplete_in_json() -> None:
    """``findings_complete`` is false for a legacy tool; ``incomplete`` is not."""
    clean = ToolResult(name="idiom-review", success=True, findings_complete=False)

    outcome = _advisory_only([clean], output="json")

    assert_that(outcome.exit_code).is_equal_to(0)
    assert_that(json.loads(outcome.output)["advisory"][0]).contains_entry(
        {"incomplete": False},
        {"findings_complete": False},
    )
