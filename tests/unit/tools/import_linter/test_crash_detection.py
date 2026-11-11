"""Tests that a ``lint-imports`` run cut short is never taken for a verdict.

``lint-imports`` prints its report from an unguarded call: an exception raised
while it prints the last contract's section reaches Python's default handler,
which writes a traceback to stderr and exits 1, the status broken contracts
also use. The stdout captured below comes from the real CLI with its renderer
fault-injected to raise after the final heading, and after that heading's
first chain; the stderr traceback is trimmed to its outer and inner frames.
Neither may pass for a verdict (#2371).
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
from lintro.utils.meaningful_run import baseline_is_eligible
from tests.unit.tools.import_linter.conftest import RunFactory

_VERSION_PATCH = "lintro.plugins.execution_preparation.verify_tool_version"

REPORT_HEAD = """
---------
Contracts
---------

Analyzed 4 files, 2 dependencies.
---------------------------------

First BROKEN
Last BROKEN

Contracts: 0 kept, 2 broken.


----------------
Broken contracts
----------------

First
-----

pk.c is not allowed to import pk.a:

-   pk.c -> pk.a (l.1)


Last
----

"""

CRASH_AFTER_FINAL_HEADING = REPORT_HEAD

CRASH_AFTER_FIRST_CHAIN = (
    REPORT_HEAD + "pk.a is not allowed to import pk.b:\n\n- pk.a -> pk.b (l.1)\n\n"
)

#: The complete report the same project prints when nothing is injected.
COMPLETE_REPORT = CRASH_AFTER_FIRST_CHAIN

UNCAUGHT_TRACEBACK = """\
Traceback (most recent call last):
  File "/venv/bin/lint-imports", line 8, in <module>
    sys.exit(lint_imports_command())
  File "/venv/lib/importlinter/application/use_cases.py", line 77, in lint_imports
    render_report(report)
  File "/venv/lib/importlinter/application/rendering.py", line 163, in \
_render_broken_contracts_details
    contract.render_broken_contract(check)
RuntimeError: renderer died mid-section
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


@pytest.mark.parametrize(
    ("stdout", "recovered"),
    [
        pytest.param(
            CRASH_AFTER_FINAL_HEADING,
            ["pk.c -> pk.a"],
            id="after-final-heading",
        ),
        pytest.param(
            CRASH_AFTER_FIRST_CHAIN,
            ["pk.c -> pk.a", "pk.a -> pk.b"],
            id="after-first-chain-of-final-section",
        ),
    ],
)
def test_crash_in_the_final_section_is_an_execution_failure(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    stdout: str,
    recovered: list[str],
) -> None:
    """A renderer crash keeps the recovered chains but is not a verdict.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        stdout: Report printed before the crash.
        recovered: Chains printed before the crash.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=stdout, stderr=UNCAUGHT_TRACEBACK),
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that([issue.message for issue in result.issues]).is_equal_to(recovered)
    assert_that(result.output).contains("RuntimeError: renderer died mid-section")


@pytest.mark.parametrize(
    "returncode",
    [
        pytest.param(-9, id="killed-by-signal"),
        pytest.param(2, id="usage-error-status"),
    ],
)
def test_complete_report_with_another_exit_status_is_an_execution_failure(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    returncode: int,
) -> None:
    """Only exit status 1 can carry a verdict, however complete the report looks.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        returncode: Exit status other than the broken-contracts one.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=returncode, stdout=COMPLETE_REPORT),
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.output).starts_with(
        f"import-linter failed (exit {returncode}) running:",
    )


def test_complete_report_with_exit_one_and_clean_stderr_is_a_verdict(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """The same report, exit 1 and an empty stderr remains a verdict.

    This is the control for the crash tests: only the crash signals differ.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=COMPLETE_REPORT),
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(2)


def test_crashed_partial_report_blocks_the_severity_baseline(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """A crash with recovered chains may not read or write a severity baseline.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    crashed = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(
            returncode=1,
            stdout=CRASH_AFTER_FIRST_CHAIN,
            stderr=UNCAUGHT_TRACEBACK,
        ),
    )
    clean_peer = ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=True, inspected_files=True)),
        name="ruff",
    )

    eligible = baseline_is_eligible(
        action=Action.CHECK,
        dry_run_preview=False,
        tool_results=[crashed, clean_peer],
    )

    assert_that(eligible).is_false()
