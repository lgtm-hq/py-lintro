"""An advisory tool lintro refused to run is an error, not a result (#3497).

The advisory text output renders only the issue table, so a refusal beside
one real finding would be unmentioned, and ``--advisory-only`` would exit 0.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

import lintro.utils.execution.advisory as advisory_module
from lintro.ai.review.error_contract import REVIEW_ERROR_EXIT_CODE
from lintro.cli import cli
from lintro.config.lintro_config import LintroConfig
from lintro.models.core.tool_result import ToolResult
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


def test_advisory_only_with_a_refusal_and_a_finding_does_not_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--advisory-only`` fails closed on a refusal even beside a real finding.

    Args:
        monkeypatch: Pytest patch fixture.
    """
    results = _run(monkeypatch, RefusingPlugin(with_finding=True))

    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.run_advisory_tools",
            return_value=results,
        ),
    ):
        outcome = CliRunner().invoke(
            cli,
            ["review", "--advisory-only", "--output", "json"],
        )

    assert_that(outcome.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    payload = json.loads(outcome.output[outcome.output.index("{") :])
    assert_that(payload["error"]["message"]).starts_with(
        "lintro refused to run tsc: ",
    )
