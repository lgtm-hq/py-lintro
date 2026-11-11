"""Tests for import-linter runs that end without a complete contract report.

``lint-imports`` exits non-zero without a lint verdict when it cannot start
(an ``ignore_imports`` entry that matches nothing under
``unmatched_ignore_imports_alerting = "error"``, an unimportable root package,
a malformed configuration) or when it crashes part-way through. The plugin must
report that as an execution failure, not as a finding, and keep the tool's own
diagnostic visible in every output format (#2371).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.tools.import_linter.definition import ImportLinterPlugin
from lintro.utils.execution.run_renderer import _display_fix_result
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.meaningful_run import baseline_is_eligible
from lintro.utils.output.file_writer import render_markdown_report
from tests.unit.tools.import_linter.conftest import RunFactory

_VERSION_PATCH = "lintro.plugins.execution_preparation.verify_tool_version"

UNMATCHED_IGNORE_MESSAGE = "No matches for ignored import pkg.nonexistent -> pkg.other."
MISSING_PACKAGE_MESSAGE = "Could not find package 'x' in your Python path."
MALFORMED_CONFIG_MESSAGE = (
    'Contract "Layers" is not configured correctly:\n'
    "    layers: This is a required field."
)
TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/venv/bin/lint-imports", line 8, in <module>\n'
    "    sys.exit(lint_imports_command())\n"
    "ModuleNotFoundError: No module named 'pkg.vendor'"
)

SUMMARY_TWO_BROKEN = """\
---------
Contracts
---------

Analyzed 6 files, 5 dependencies.
---------------------------------

Layered architecture BROKEN
Forbidden vendor BROKEN

Contracts: 0 kept, 2 broken.
"""

BROKEN_HEADING = """
----------------
Broken contracts
----------------
"""

FIRST_CONTRACT_CHAIN = """
Layered architecture
--------------------

layered.storage is not allowed to import layered.api:

- layered.storage -> layered.api (l.6)

"""


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


def _assert_execution_failure(result: ToolResult, *, issues_count: int) -> None:
    """Assert the result is an execution failure rather than a lint verdict.

    Args:
        result: Result under test.
        issues_count: Number of genuinely parsed chains the result keeps.
    """
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues_count).is_equal_to(issues_count)
    assert_that(result.issues).is_length(issues_count)
    assert_that(result.output).starts_with("import-linter failed (exit 1) running:")


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(UNMATCHED_IGNORE_MESSAGE, id="unmatched-ignore-imports"),
        pytest.param(MISSING_PACKAGE_MESSAGE, id="missing-root-package"),
        pytest.param(MALFORMED_CONFIG_MESSAGE, id="malformed-config"),
        pytest.param(TRACEBACK, id="crash-without-report"),
    ],
)
def test_run_without_report_is_an_execution_failure(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    message: str,
) -> None:
    """An exit-1 run with no contract report fails with its reason and no issue.

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

    _assert_execution_failure(result, issues_count=0)
    assert_that(result.output).contains(
        f"lint-imports --config {config} --no-logo --no-cache",
        message,
    )


def test_execution_failure_keeps_stdout_and_stderr(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """Both captured streams reach the output, stdout first.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(
            returncode=1,
            stdout="Building import graph...\n",
            stderr=f"{TRACEBACK}\n",
        ),
    )

    _assert_execution_failure(result, issues_count=0)
    assert_that(result.output).ends_with(f"Building import graph...\n{TRACEBACK}")


def test_execution_failure_without_output_says_so(
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

    _assert_execution_failure(result, issues_count=0)
    assert_that(result.output).ends_with("lint-imports produced no output.")


@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("\nBroken contracts\n", id="heading-without-underline"),
        pytest.param(
            "Contracts: 0 kept, 0 broken.\n" + TRACEBACK,
            id="summary-then-traceback",
        ),
        pytest.param(SUMMARY_TWO_BROKEN + BROKEN_HEADING, id="heading-without-chains"),
        pytest.param(SUMMARY_TWO_BROKEN, id="broken-summary-without-heading"),
        pytest.param("Contracts: 1 kept, 0 broken.\n", id="nothing-broken-yet-exit-1"),
    ],
)
def test_incomplete_report_is_an_execution_failure(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    stdout: str,
) -> None:
    """A partial report on a non-zero exit is not a verdict, and is kept.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        stdout: Partial report printed before the run ended.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=stdout),
    )

    _assert_execution_failure(result, issues_count=0)
    assert_that(result.output).ends_with(stdout.strip())


def test_truncated_report_keeps_recovered_chains(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """Chains printed before a crash are kept beside the diagnostic.

    The summary says two contracts broke but only one contract's chains were
    printed before the traceback, so the findings are incomplete.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    stdout = SUMMARY_TWO_BROKEN + BROKEN_HEADING + FIRST_CONTRACT_CHAIN

    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=stdout, stderr=TRACEBACK),
    )

    _assert_execution_failure(result, issues_count=1)
    assert_that(result.issues[0].message).is_equal_to(
        "layered.storage -> layered.api",
    )
    assert_that(result.output).contains(TRACEBACK)


def test_complete_broken_report_is_a_verdict(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    broken_output: str,
) -> None:
    """A complete broken-contracts report stays an ordinary lint verdict.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        broken_output: Output with one broken contract and its chain.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=broken_output),
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that([issue.get_code() for issue in result.issues]).is_equal_to(
        ["Layered architecture"],
    )
    assert_that(result.output).is_equal_to(broken_output)


def test_clean_exit_stays_successful(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    kept_output: str,
) -> None:
    """Exit 0 with every contract kept is a clean, complete run.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        kept_output: Output where the only contract is kept.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=0, stdout=kept_output),
    )

    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.issues_count).is_equal_to(0)


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


@pytest.fixture
def clean_peer_result() -> ToolResult:
    """Provide a complete, clean result from another tool in the same run.

    Returns:
        A successful result that inspected files.
    """
    return ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=True, inspected_files=True)),
        name="ruff",
    )


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        pytest.param(f"\n{UNMATCHED_IGNORE_MESSAGE}\n", "", id="no-report"),
        pytest.param(
            SUMMARY_TWO_BROKEN + BROKEN_HEADING + FIRST_CONTRACT_CHAIN,
            TRACEBACK,
            id="truncated-report-with-recovered-chains",
        ),
    ],
)
def test_execution_failure_blocks_the_severity_baseline(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    clean_peer_result: ToolResult,
    stdout: str,
    stderr: str,
) -> None:
    """A run containing the failure may not read or write a severity baseline.

    The recovered-chains case matters most: its parsed issues would otherwise
    make the failure look like an ordinary verdict and record an undercount.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        clean_peer_result: Clean result from another tool.
        stdout: Output the failed run printed.
        stderr: Error stream the failed run printed.
    """
    failed = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=stdout, stderr=stderr),
    )

    eligible = baseline_is_eligible(
        action=Action.CHECK,
        dry_run_preview=False,
        tool_results=[failed, clean_peer_result],
    )

    assert_that(eligible).is_false()


def test_broken_contract_verdict_keeps_the_severity_baseline(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    broken_output: str,
    clean_peer_result: ToolResult,
) -> None:
    """A real broken-contract verdict leaves the run baseline-eligible.

    This is the control for the test above: eligibility is lost because of
    the execution failure, not because import-linter failed at all.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        broken_output: Output with one broken contract and its chain.
        clean_peer_result: Clean result from another tool.
    """
    verdict = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=broken_output),
    )

    eligible = baseline_is_eligible(
        action=Action.CHECK,
        dry_run_preview=False,
        tool_results=[verdict, clean_peer_result],
    )

    assert_that(eligible).is_true()


def _console_text(result: ToolResult) -> str:
    """Render one result through the console display path.

    Args:
        result: Result to render.

    Returns:
        Everything the console would print for it, joined by newlines.
    """
    lines: list[str] = []

    def emit(text: str = "", **_: object) -> None:
        lines.append(text)

    def emit_success(message: str = "", **_: object) -> None:
        lines.append(message)

    _display_fix_result(
        result,
        output_format="grid",
        raw_output=False,
        console_output_func=emit,
        success_func=emit_success,
        action=Action.CHECK,
    )
    return "\n".join(lines)


def test_console_shows_the_reason(aborted_result: ToolResult) -> None:
    """The console names the failure, the command and the tool's message.

    Args:
        aborted_result: Result of an unmatched-``ignore_imports`` run.
    """
    text = _console_text(aborted_result)

    assert_that(text).contains(
        "import-linter failed (exit 1) running:",
        "lint-imports --config",
        UNMATCHED_IGNORE_MESSAGE,
    )
    assert_that(text).does_not_contain("No issues found")


def test_console_keeps_the_reason_beside_recovered_chains(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """Recovered chains do not push the diagnostic off the console.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(
            returncode=1,
            stdout=SUMMARY_TWO_BROKEN + BROKEN_HEADING + FIRST_CONTRACT_CHAIN,
            stderr=TRACEBACK,
        ),
    )

    text = _console_text(result)

    assert_that(text).contains("Layered architecture", "import-linter failed")
    assert_that(text).contains("ModuleNotFoundError: No module named 'pkg.vendor'")


def test_markdown_report_shows_the_reason(aborted_result: ToolResult) -> None:
    """The Markdown report says the tool could not finish and quotes why.

    Args:
        aborted_result: Result of an unmatched-``ignore_imports`` run.
    """
    report = render_markdown_report([aborted_result], action=Action.CHECK)

    assert_that(report).contains(
        "**import-linter could not complete its check; findings are incomplete.**",
        "import-linter failed (exit 1) running:",
        UNMATCHED_IGNORE_MESSAGE,
    )
    assert_that(report).does_not_contain("No issues found")


def test_json_report_flags_the_failure(aborted_result: ToolResult) -> None:
    """The JSON report flags the execution failure and carries the reason.

    Args:
        aborted_result: Result of an unmatched-``ignore_imports`` run.
    """
    payload = serialize_tool_result(aborted_result, action=Action.CHECK)

    assert_that(payload["success"]).is_false()
    assert_that(payload["execution_failed"]).is_true()
    assert_that(payload["issues_count"]).is_equal_to(0)
    assert_that(payload).does_not_contain_key("issues")
    assert_that(payload["output"]).starts_with("import-linter failed (exit 1)")
    assert_that(payload["output"]).ends_with(UNMATCHED_IGNORE_MESSAGE)
