"""Tests for how complete ``lint-imports`` reports are recognised and parsed.

``ALL_CONTRACT_TYPES_OUTPUT`` was captured from the real ``lint-imports`` for a
package breaking one contract of every built-in type, with two ``forbidden``
contracts that share a name. Completeness is judged by the report's structure
(contract sections against the summary's broken count), never by how many
distinct contract names the parsed chains carry (#2371).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.import_linter.import_linter_parser import (
    parse_import_linter_report,
)
from lintro.tools.import_linter.definition import ImportLinterPlugin
from lintro.utils.execution.run_renderer import _display_fix_result
from tests.unit.tools.import_linter.conftest import RunFactory

_VERSION_PATCH = "lintro.plugins.execution_preparation.verify_tool_version"

ALL_CONTRACT_TYPES_OUTPUT = """\
---------
Contracts
---------

Analyzed 7 files, 5 dependencies.
---------------------------------

Same name BROKEN
Same name BROKEN
Layers BROKEN
Indep BROKEN
Prot BROKEN
Acyc BROKEN

Contracts: 0 kept, 6 broken.


----------------
Broken contracts
----------------

Same name
---------

pk.a is not allowed to import pk.b:

-   pk.a -> pk.b (l.1)


Same name
---------

pk.c is not allowed to import pk.a:

-   pk.c -> pk.a (l.1)


Layers
------

pk.b is not allowed to import pk.a:

- pk.b -> pk.a (l.1)


Indep
-----

pk.b is not allowed to import pk.a:

- pk.b -> pk.a (l.1)


pk.a is not allowed to import pk.b:

- pk.a -> pk.b (l.1)


Prot
----

Illegal imports of protected package pk.a:

- pk.c -> pk.a (l.1)


Acyc
----

No cycles are allowed in pk.
It could be made acyclic by removing 1 dependency:

- .b -> .a (1 import)

No cycles are allowed in pk.sub.
It could be made acyclic by removing 1 dependency:

- .y -> .x (1 import)

"""

UNREADABLE_SECTION_OUTPUT = """\
Contracts: 0 kept, 2 broken.

----------------
Broken contracts
----------------

Layers
------

- pk.b -> pk.a (l.1)


Custom rule
-----------

pk.b breaks a rule this parser cannot read: see ~~pk.b~~.
"""

WARNING_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/venv/lib/site.py", line 1, in <module>\n'
    "DeprecationWarning: printed by a plugin, then ignored"
)


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


def test_every_built_in_contract_type_is_parsed() -> None:
    """Each built-in contract type's chains become issues, in report order."""
    report = parse_import_linter_report(ALL_CONTRACT_TYPES_OUTPUT)

    assert_that(
        [(issue.get_code(), issue.message) for issue in report.issues],
    ).is_equal_to(
        [
            ("Same name", "pk.a -> pk.b"),
            ("Same name", "pk.c -> pk.a"),
            ("Layers", "pk.b -> pk.a"),
            ("Indep", "pk.b -> pk.a"),
            ("Indep", "pk.a -> pk.b"),
            ("Prot", "pk.c -> pk.a"),
            ("Acyc", "pk.b -> pk.a"),
            ("Acyc", "pk.sub.y -> pk.sub.x"),
        ],
    )
    assert_that(report.sections).is_equal_to(6)
    assert_that(report.unparsed_sections).is_equal_to(0)


def test_contracts_sharing_a_name_make_a_complete_report(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """Six sections for six broken contracts is a verdict despite a shared name.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=ALL_CONTRACT_TYPES_OUTPUT),
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.issues_count).is_equal_to(8)


def test_unreadable_section_stays_a_verdict_and_keeps_its_text(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
) -> None:
    """A complete report with an unparseable section is a verdict, shown in full.

    The section counts as a parse failure, so coverage is unknown, and the
    console prints the raw report beneath the chains that were parsed.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=UNREADABLE_SECTION_OUTPUT),
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.inspected_files).is_none()
    assert_that([issue.message for issue in result.issues]).is_equal_to(
        ["pk.b -> pk.a"],
    )

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

    console = "\n".join(lines)
    assert_that(console).contains("pk.b breaks a rule this parser cannot read")


def test_traceback_before_the_report_does_not_discredit_it(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    broken_output: str,
) -> None:
    """A warning traceback printed ahead of a complete report leaves a verdict.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        broken_output: Output with one broken contract and its chain.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=f"{WARNING_TRACEBACK}\n{broken_output}"),
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_traceback_on_stderr_fails_a_complete_looking_report(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    broken_output: str,
) -> None:
    """Any traceback on stderr with a non-zero exit is an execution failure.

    ``lint-imports`` prints its report from an unguarded call, so an uncaught
    exception while printing it lands on stderr and exits 1 with the report on
    stdout looking complete. The check fails closed on that signal.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        broken_output: Output with one broken contract and its chain.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=broken_output, stderr=WARNING_TRACEBACK),
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).ends_with(WARNING_TRACEBACK)


def test_traceback_after_the_summary_is_a_crash(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    make_run: RunFactory,
    broken_output: str,
) -> None:
    """A traceback printed after the summary means the report did not finish.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
        make_run: Factory for fake ``lint-imports`` runs.
        broken_output: Output with one broken contract and its chain.
    """
    result = _check(
        import_linter_plugin,
        project_with_contracts,
        make_run(returncode=1, stdout=f"{broken_output}{WARNING_TRACEBACK}\n"),
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains(WARNING_TRACEBACK)
