"""Unit tests for the golangci-lint plugin execution paths (mocked subprocess)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.golangci_lint.golangci_lint_issue import GolangciLintIssue
from lintro.tools.definitions.golangci_lint import GolangciLintPlugin
from tests.unit.tools.golangci_lint.conftest import (
    GOLANGCI_JSON_NO_ISSUES,
    GOLANGCI_JSON_ONE_ISSUE,
    GOLANGCI_JSON_TWO_ISSUES,
)


def _make_go_module(root: Path) -> None:
    """Create a minimal Go module with a source file under ``root``.

    Args:
        root: Directory to populate with go.mod and main.go.
    """
    (root / "go.mod").write_text("module example.com/fixture\n\ngo 1.21\n")
    (root / "main.go").write_text("package main\n\nfunc main() {}\n")


def test_check_reports_issues(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """Check parses issues from golangci-lint JSON output.

    Args:
        golangci_lint_plugin: Plugin under test.
        tmp_path: Temporary directory for the Go module.
    """
    _make_go_module(tmp_path)

    # golangci-lint exits non-zero when issues are found.
    with patch.object(
        golangci_lint_plugin,
        "_run_subprocess",
        return_value=(False, GOLANGCI_JSON_TWO_ISSUES),
    ):
        result = golangci_lint_plugin.check([str(tmp_path)], {})

    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.success).is_false()
    assert_that(result.issues).is_not_none()
    issues = cast(list[GolangciLintIssue], result.issues)
    assert_that([i.code for i in issues]).contains(
        "errcheck",
        "ineffassign",
    )


def test_check_clean_module(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """Check succeeds with zero issues on a clean module.

    Args:
        golangci_lint_plugin: Plugin under test.
        tmp_path: Temporary directory for the Go module.
    """
    _make_go_module(tmp_path)

    with patch.object(
        golangci_lint_plugin,
        "_run_subprocess",
        return_value=(True, GOLANGCI_JSON_NO_ISSUES),
    ):
        result = golangci_lint_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_skips_without_go_mod(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """Check skips cleanly when a Go file has no enclosing module.

    Args:
        golangci_lint_plugin: Plugin under test.
        tmp_path: Temporary directory with a .go file but no go.mod.
    """
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n")

    called = False

    def _fail(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
        nonlocal called
        called = True
        return (True, "")

    with patch.object(golangci_lint_plugin, "_run_subprocess", side_effect=_fail):
        result = golangci_lint_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("go.mod")
    assert_that(called).is_false()


def test_check_no_go_files(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """Check returns an early no-files result when no Go files are present.

    Args:
        golangci_lint_plugin: Plugin under test.
        tmp_path: Temporary directory with a non-Go file.
    """
    (tmp_path / "readme.txt").write_text("not go\n")
    result = golangci_lint_plugin.check([str(tmp_path)], {})
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_fix_counts_fixed_issues(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """Fix computes initial/fixed/remaining counts across the run sequence.

    Args:
        golangci_lint_plugin: Plugin under test.
        tmp_path: Temporary directory for the Go module.
    """
    _make_go_module(tmp_path)

    # Sequence: initial check (2 issues), fix run, re-check (0 issues).
    outputs = [
        (False, GOLANGCI_JSON_TWO_ISSUES),
        (True, ""),
        (True, GOLANGCI_JSON_NO_ISSUES),
    ]

    with patch.object(
        golangci_lint_plugin,
        "_run_subprocess",
        side_effect=outputs,
    ):
        result = golangci_lint_plugin.fix([str(tmp_path)], {})

    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


def test_fix_failure_is_not_masked_as_success(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """A failed --fix run with no parseable re-check issues reports failure.

    Args:
        golangci_lint_plugin: Plugin under test.
        tmp_path: Temporary directory for the Go module.
    """
    _make_go_module(tmp_path)

    # Sequence: initial check (2 issues), fix run FAILS (config/build error with
    # non-JSON output), re-check yields no parseable issues.
    fix_error = 'level=error msg="can\'t run linter: build error"'
    outputs = [
        (False, GOLANGCI_JSON_TWO_ISSUES),
        (False, fix_error),
        (False, ""),
    ]

    with patch.object(
        golangci_lint_plugin,
        "_run_subprocess",
        side_effect=outputs,
    ):
        result = golangci_lint_plugin.fix([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.output).contains("build error")


def test_fix_failure_output_preserved_with_remaining_issues(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """A failed --fix run surfaces its output even when issues still remain.

    Regression guard: when ``golangci-lint run --fix`` exits non-zero (config/
    build/fixer error) and the follow-up check still parses remaining issues,
    the fix command's diagnostic output must not be dropped.

    Args:
        golangci_lint_plugin: Plugin under test.
        tmp_path: Temporary directory for the Go module.
    """
    _make_go_module(tmp_path)

    # Sequence: initial check (2 issues), fix run FAILS (build error), re-check
    # still parses one remaining issue.
    fix_error = 'level=error msg="can\'t run linter: build error"'
    outputs = [
        (False, GOLANGCI_JSON_TWO_ISSUES),
        (False, fix_error),
        (False, GOLANGCI_JSON_ONE_ISSUE),
    ]

    with patch.object(
        golangci_lint_plugin,
        "_run_subprocess",
        side_effect=outputs,
    ):
        result = golangci_lint_plugin.fix([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.output).contains("build error")


def test_fix_skips_without_go_mod(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """Fix skips cleanly when there is no enclosing Go module.

    Args:
        golangci_lint_plugin: Plugin under test.
        tmp_path: Temporary directory with a .go file but no go.mod.
    """
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n")

    with patch.object(
        golangci_lint_plugin,
        "_run_subprocess",
        return_value=(True, ""),
    ):
        result = golangci_lint_plugin.fix([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_find_module_roots_returns_each_module(tmp_path: Path) -> None:
    """Two sibling modules without a parent go.mod both become roots."""
    from lintro.tools.definitions.golangci_lint import _find_go_module_roots

    mod_a = tmp_path / "svc-a"
    mod_b = tmp_path / "svc-b"
    mod_a.mkdir()
    mod_b.mkdir()
    _make_go_module(mod_a)
    _make_go_module(mod_b)

    roots = _find_go_module_roots(
        [str(mod_a / "main.go"), str(mod_b / "main.go")],
    )

    assert_that(roots).is_length(2)
    assert_that([r.name for r in roots]).contains("svc-a", "svc-b")


def test_check_covers_all_selected_modules(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """Check runs once per module and aggregates issues across modules."""
    mod_a = tmp_path / "svc-a"
    mod_b = tmp_path / "svc-b"
    mod_a.mkdir()
    mod_b.mkdir()
    _make_go_module(mod_a)
    _make_go_module(mod_b)

    calls: list[str] = []

    def _fake_run(**kwargs: Any) -> tuple[bool, str]:
        calls.append(kwargs["cwd"])
        return (False, GOLANGCI_JSON_TWO_ISSUES)

    with patch(
        "lintro.tools.definitions.golangci_lint.run_subprocess_with_timeout",
        side_effect=lambda **kwargs: _fake_run(**kwargs),
    ):
        result = golangci_lint_plugin.check(
            [str(mod_a / "main.go"), str(mod_b / "main.go")],
            {},
        )

    assert_that(calls).is_length(2)
    assert_that(result.issues_count).is_equal_to(4)
    assert_that(result.success).is_false()
