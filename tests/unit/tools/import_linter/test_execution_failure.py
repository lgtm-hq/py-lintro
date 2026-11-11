"""Tests for import-linter runs that fail before producing a contract report.

``lint-imports`` exits non-zero without evaluating any contract when an
``ignore_imports`` entry matches nothing under
``unmatched_ignore_imports_alerting = "error"``, when the root package cannot
be imported, or when the configuration is malformed. The plugin must name that
failure and count it as one issue rather than reporting a bare, zero-issue
failure (#2371).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.import_linter.import_linter_issue import (
    IMPORT_LINTER_EXECUTION_ERROR_CODE,
    ImportLinterIssue,
)
from lintro.tools.import_linter.definition import ImportLinterPlugin
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.output.file_writer import (
    format_tool_output,
    render_markdown_report,
)
from tests.unit.tools.import_linter.conftest import RunFactory

_VERSION_PATCH = "lintro.plugins.execution_preparation.verify_tool_version"

UNMATCHED_IGNORE_MESSAGE = "No matches for ignored import pkg.nonexistent -> pkg.other."
MISSING_PACKAGE_MESSAGE = "Could not find package 'x' in your Python path."


def _check(
    plugin: ImportLinterPlugin,
    project: Path,
    run: SubprocessResult,
) -> ToolResult:
    """Run ``check()`` against a project with a faked ``lint-imports`` run.

    Args:
        plugin: Plugin under test.
        project: Project root carrying import-linter config.
        run: Fake subprocess result the plugin receives.

    Returns:
        The plugin's result.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(plugin, "_run_subprocess_result", return_value=run),
    ):
        return plugin.check([str(project)], {})


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(UNMATCHED_IGNORE_MESSAGE, id="unmatched-ignore-imports"),
        pytest.param(MISSING_PACKAGE_MESSAGE, id="missing-root-package"),
    ],
)
def test_aborted_run_reports_one_labelled_issue(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    message: str,
) -> None:
    """An exit-1 run with no contract report fails with one labelled issue.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        message: The tool's own diagnostic, as ``lint-imports`` prints it.
    """
    config = project_with_contracts / "pyproject.toml"

    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=f"\n{message}\n"),
    )

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    output = result.output or ""
    assert_that(output).starts_with("import-linter failed (exit 1) running:")
    assert_that(output).contains(
        f"lint-imports --config {config} --no-logo --no-cache",
    )
    assert_that(output).contains(message)
    issue = result.issues[0]
    assert isinstance(issue, ImportLinterIssue)  # nosec B101 - narrow type for mypy
    assert_that(issue.code).is_equal_to(IMPORT_LINTER_EXECUTION_ERROR_CODE)
    assert_that(issue.file).is_equal_to(str(config))
    assert_that(issue.message).is_equal_to(
        f"lint-imports exited 1 before checking contracts: {message}",
    )


def test_aborted_run_keeps_stdout_and_stderr(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """Both captured streams reach the output and the issue, in order.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(
            returncode=2,
            stdout="Building import graph...\n",
            stderr="ModuleNotFoundError: No module named 'requests'\n",
        ),
    )

    expected_reason = (
        "Building import graph...\nModuleNotFoundError: No module named 'requests'"
    )
    assert_that(result.output).ends_with(expected_reason)
    assert_that(result.output).starts_with("import-linter failed (exit 2)")
    assert_that(result.issues[0].message).ends_with(expected_reason)


def test_aborted_run_without_output_says_so(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """A silent non-zero exit is named rather than left blank.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1),
    )

    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).ends_with("lint-imports produced no output.")
    assert_that(result.issues[0].message).ends_with("lint-imports produced no output.")


def test_broken_contracts_produce_no_execution_issue(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    broken_output: str,
) -> None:
    """A real contract report keeps one issue per chain and no synthetic issue.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        broken_output: Output with one broken contract.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=broken_output),
    )

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that([issue.get_code() for issue in result.issues]).is_equal_to(
        ["Layered architecture"],
    )
    assert_that(result.output).is_equal_to(broken_output)


def test_summary_without_broken_section_is_not_an_aborted_run(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    kept_output: str,
) -> None:
    """A non-zero exit that still printed the contract summary is passed through.

    The summary line alone proves the contracts were evaluated, so the run is
    not reclassified as an execution failure.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        kept_output: Output carrying a ``Contracts: 1 kept, 0 broken.`` line.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=kept_output),
    )

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_equal_to(kept_output)


def test_clean_exit_stays_successful(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """Exit 0 is a clean run even when the output has no contract report.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=0, stdout=""),
    )

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_empty()


@pytest.fixture
def aborted_result(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> ToolResult:
    """Provide the result of an unmatched-``ignore_imports`` run.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.

    Returns:
        The failed ToolResult.
    """
    return _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=f"\n{UNMATCHED_IGNORE_MESSAGE}\n"),
    )


@pytest.mark.parametrize("output_format", ["grid", "markdown", "json"])
def test_reason_survives_console_formatting(
    aborted_result: ToolResult,
    output_format: str,
) -> None:
    """The per-tool console output carries the reason in every format.

    ``format_tool_output`` renders only the issue table once issues exist,
    so the reason must be in the issue, not only in ``ToolResult.output``.

    Args:
        aborted_result: Result of an unmatched-``ignore_imports`` run.
        output_format: Output format under test.
    """
    rendered = format_tool_output(
        tool_name="import-linter",
        output=aborted_result.output or "",
        output_format=output_format,
        issues=aborted_result.issues,
        success=aborted_result.success,
        issues_count=aborted_result.issues_count,
    )

    # Grid tables wrap long cells onto several rows, so only compare fragments
    # short enough to stay on one row, with whitespace collapsed.
    flattened = " ".join(rendered.replace("|", " ").split())
    assert_that(flattened).contains("lint-imports exited 1 before checking contracts")
    assert_that(flattened).contains("pkg.nonexistent -> pkg.other")
    assert_that(flattened).contains("Execution")


def test_reason_reaches_markdown_report(aborted_result: ToolResult) -> None:
    """The Markdown report counts the failure and shows its reason.

    Args:
        aborted_result: Result of an unmatched-``ignore_imports`` run.
    """
    report = render_markdown_report([aborted_result], action=Action.CHECK)

    assert_that(report).contains("| import-linter | 1 |")
    assert_that(report).contains(
        f"{IMPORT_LINTER_EXECUTION_ERROR_CODE} | "
        f"lint-imports exited 1 before checking contracts: {UNMATCHED_IGNORE_MESSAGE}",
    )


def test_reason_reaches_json_report(aborted_result: ToolResult) -> None:
    """The JSON report has a structured issue and the labelled output.

    Args:
        aborted_result: Result of an unmatched-``ignore_imports`` run.
    """
    payload = serialize_tool_result(aborted_result, action=Action.CHECK)

    assert_that(payload["success"]).is_false()
    assert_that(payload["issues_count"]).is_equal_to(1)
    assert_that(payload["output"]).starts_with("import-linter failed (exit 1)")
    issue = payload["issues"][0]
    assert_that(issue["code"]).is_equal_to(IMPORT_LINTER_EXECUTION_ERROR_CODE)
    assert_that(issue["message"]).ends_with(UNMATCHED_IGNORE_MESSAGE)
