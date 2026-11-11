"""Integration tests running the real ``lint-imports`` binary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.import_linter.import_linter_issue import ImportLinterIssue
from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# import-linter ships its CLI as ``lint-imports``, the same binary the plugin
# invokes; the version floor is looked up under the lintro tool name.
pytestmark = require_tool(
    "lint-imports",
    label="import-linter",
    pin="import_linter",
)


def test_check_reports_the_broken_chain(
    get_plugin: Callable[[str], BaseToolPlugin],
    broken_contract_project: str,
) -> None:
    """A deliberately broken layers contract yields one issue with its chain.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        broken_contract_project: Staged project with a broken contract.
    """
    plugin = get_plugin("import-linter")
    result = plugin.check([broken_contract_project], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("import-linter")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issue = result.issues[0]
    assert isinstance(issue, ImportLinterIssue)  # nosec B101 - narrow type for mypy
    assert_that(issue.code).is_equal_to("Layered architecture")
    assert_that(issue.file).is_equal_to("layered.storage")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.message).is_equal_to(
        "layered.storage -> layered.helpers -> layered.compat -> layered.api",
    )
    # BaseIssue defaults to WARNING, so a dropped override would pass unnoticed
    # without this: a broken contract must surface as an error.
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_check_passes_when_contracts_are_kept(
    get_plugin: Callable[[str], BaseToolPlugin],
    kept_contract_project: str,
) -> None:
    """A project honouring its layers contract passes with no issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        kept_contract_project: Staged project with a kept contract.
    """
    plugin = get_plugin("import-linter")
    result = plugin.check([kept_contract_project], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    # Success plus zero issues is also the shape of a skip and of the no-config
    # path, so prove the binary ran and really evaluated the contract.
    assert_that(result.skipped).is_false()
    assert_that(result.output).contains("1 kept, 0 broken")


def test_check_without_any_config_is_clean_not_skipped(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A Python project with no import-linter config reports a clean result.

    The native tool errors when it can find no config at all, so this asserts
    Lintro's deliberate divergence, and asserts the *reason*: the run must be a
    real clean result, not a silent skip that happens to look identical.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    plugin = get_plugin("import-linter")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.skipped).is_false()
    assert_that(result.output).contains("No import-linter configuration found")


def test_check_with_an_empty_contract_set_runs_the_tool(
    get_plugin: Callable[[str], BaseToolPlugin],
    empty_contract_set_project: str,
) -> None:
    """The dogfood shape really runs the binary and reports zero contracts.

    This repo enables import-linter with ``root_package`` and no contracts
    (#2289; contracts land in #2290). That must be a genuine 0-kept/0-broken
    run, not a skip — otherwise the tool would look green while never
    executing, and #2290's contracts would land on an inert runner.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        empty_contract_set_project: Staged project with a contract-free config.
    """
    plugin = get_plugin("import-linter")
    result = plugin.check([empty_contract_set_project], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.skipped).is_false()
    # Proof the binary ran: only lint-imports emits the summary line.
    assert_that(result.output).contains("0 kept, 0 broken")


def test_check_names_an_unmatched_ignore_failure(
    get_plugin: Callable[[str], BaseToolPlugin],
    unmatched_ignore_project: str,
) -> None:
    """A run aborted by an unmatched ``ignore_imports`` entry carries its reason.

    ``lint-imports`` exits 1 and prints only its diagnostic, with no contract
    report. Lintro must report an execution failure that names the reason, not
    a bare failure and not a fabricated finding (#2371).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        unmatched_ignore_project: Staged project with an unmatched ignore.
    """
    plugin = get_plugin("import-linter")
    result = plugin.check([unmatched_ignore_project], {})

    diagnostic = (
        "No matches for ignored import unmatchedpkg.storage -> unmatchedpkg.api."
    )
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_empty()
    assert_that(result.output).starts_with("import-linter failed (exit 1) running:")
    assert_that(result.output).contains(".importlinter --no-logo --no-cache")
    assert_that(result.output).ends_with(diagnostic)


def test_contracts_sharing_a_name_are_both_reported(
    get_plugin: Callable[[str], BaseToolPlugin],
    duplicate_names_project: str,
) -> None:
    """Two broken contracts with the same name are a verdict with two chains.

    Completeness is judged by contract sections, not by distinct names, so a
    shared name must not turn a full report into an execution failure.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        duplicate_names_project: Staged project with two same-named contracts.
    """
    plugin = get_plugin("import-linter")
    result = plugin.check([duplicate_names_project], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(sorted(issue.message for issue in result.issues)).is_equal_to(
        [
            "dupnames.services -> dupnames.api",
            "dupnames.storage -> dupnames.api",
        ],
    )
    assert_that({issue.get_code() for issue in result.issues}).is_equal_to(
        {"No upward imports"},
    )


def test_acyclic_siblings_cycles_are_reported(
    get_plugin: Callable[[str], BaseToolPlugin],
    acyclic_siblings_project: str,
) -> None:
    """``acyclic_siblings`` relative hops become absolute chains, one per cycle.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        acyclic_siblings_project: Staged project with two sibling cycles.
    """
    plugin = get_plugin("import-linter")
    result = plugin.check([acyclic_siblings_project], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.parse_failures_count).is_none()
    assert_that([issue.message for issue in result.issues]).is_equal_to(
        ["cyclic.b -> cyclic.a", "cyclic.sub.y -> cyclic.sub.x"],
    )
    assert_that([issue.file for issue in result.issues]).is_equal_to(
        ["cyclic.b", "cyclic.sub.y"],
    )
