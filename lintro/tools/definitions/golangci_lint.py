"""golangci-lint tool definition.

golangci-lint is the de-facto Go meta-linter, running 100+ sub-linters
(errcheck, staticcheck, ineffassign, govet, ...) in parallel with caching.
It runs via ``golangci-lint run`` and requires a Go module context (a
``go.mod`` file). This plugin targets golangci-lint v2, whose CLI replaced the
v1 ``--out-format`` flag with ``--output.<format>.path`` options.
"""

# mypy: ignore-errors
# Note: mypy errors are suppressed because lintro runs mypy from the file's
# directory, breaking package resolution. When run properly (mypy lintro/...),
# this file passes.

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.golangci_lint.golangci_lint_issue import GolangciLintIssue
from lintro.parsers.golangci_lint.golangci_lint_parser import (
    parse_golangci_lint_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
)
from lintro.tools.core.timeout_utils import (
    create_timeout_result,
    run_subprocess_with_timeout,
)

# Constants for golangci-lint configuration
GOLANGCI_LINT_DEFAULT_TIMEOUT: int = 120
GOLANGCI_LINT_DEFAULT_PRIORITY: int = 85
GOLANGCI_LINT_FILE_PATTERNS: list[str] = ["*.go", "go.mod"]


def _find_go_module_roots(paths: list[str]) -> list[Path]:
    """Return the distinct ``go.mod`` module roots covering the given paths.

    When the selected paths span multiple Go modules that share a common
    parent with its own ``go.mod``, that single parent is returned; otherwise
    each distinct module root is returned so every selected module is linted.

    Args:
        paths: List of file paths to search from.

    Returns:
        Sorted list of module root directories (empty when none found).
    """
    roots: list[Path] = []
    for raw_path in paths:
        current = Path(raw_path).resolve()
        if current.is_file():
            current = current.parent
        for candidate in [current] + list(current.parents):
            if (candidate / "go.mod").exists():
                roots.append(candidate)
                break

    if not roots:
        return []

    # Even when a parent module exists, nested modules are distinct
    # go.mod roots and must each be linted independently.
    return sorted(set(roots))


def _rebase_issue_paths(
    *,
    issues: list[GolangciLintIssue],
    module_root: Path,
) -> None:
    """Anchor each issue's relative file path to its module root, in place.

    golangci-lint reports ``Pos.Filename`` relative to the module root it ran
    in. When findings from several module roots are merged, identical relative
    names (e.g. ``main.go`` in two sibling modules) become ambiguous, so each
    path is rewritten to an absolute path under its module root. The synthetic
    ``(module)`` placeholder used for position-less findings and paths that are
    already absolute are left untouched.

    Args:
        issues: Parsed issues to rewrite in place.
        module_root: Directory golangci-lint ran in for these issues.
    """
    for issue in issues:
        file_path = issue.file
        if file_path and file_path != "(module)" and not Path(file_path).is_absolute():
            issue.file = str(module_root / file_path)


def _merge_fix_results(*, name: str, results: list[ToolResult]) -> ToolResult:
    """Merge per-module fix results into a single aggregate result.

    Args:
        name: Tool name for the aggregate result.
        results: One fix ToolResult per Go module root.

    Returns:
        Aggregate ToolResult (success only when every module succeeded).
    """
    issues: list[Any] = []
    initial_issues: list[Any] = []
    outputs: list[str] = []
    for result in results:
        issues.extend(result.issues or [])
        initial_issues.extend(result.initial_issues or [])
        if result.output:
            outputs.append(result.output)
    return ToolResult(
        name=name,
        success=all(r.success for r in results),
        output="\n".join(outputs) if outputs else None,
        issues_count=sum(r.issues_count or 0 for r in results),
        issues=issues,
        initial_issues_count=sum(r.initial_issues_count or 0 for r in results),
        fixed_issues_count=sum(r.fixed_issues_count or 0 for r in results),
        remaining_issues_count=sum(r.remaining_issues_count or 0 for r in results),
        initial_issues=initial_issues if initial_issues else None,
    )


def _build_golangci_lint_command(fix: bool = False) -> list[str]:
    """Build the ``golangci-lint run`` command.

    Args:
        fix: Whether to include the ``--fix`` flag.

    Returns:
        List of command arguments.
    """
    cmd = [
        "golangci-lint",
        "run",
        "--output.json.path",
        "stdout",
        "--show-stats=false",
    ]
    if fix:
        cmd.append("--fix")
    cmd.append("./...")
    return cmd


@register_tool
@dataclass
class GolangciLintPlugin(BaseToolPlugin):
    """golangci-lint Go meta-linter plugin.

    Integrates golangci-lint with lintro for checking Go code across dozens of
    sub-linters. Requires a Go module (``go.mod``); non-Go projects are skipped
    cleanly.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="golangci_lint",
            description=(
                "Go meta-linter running 100+ linters (errcheck, staticcheck, "
                "ineffassign, govet, ...) in parallel"
            ),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=GOLANGCI_LINT_FILE_PATTERNS,
            priority=GOLANGCI_LINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[
                ".golangci.yml",
                ".golangci.yaml",
                ".golangci.toml",
                ".golangci.json",
            ],
            version_command=["golangci-lint", "version"],
            min_version=get_min_version(ToolName.GOLANGCI_LINT),
            default_options={
                "timeout": GOLANGCI_LINT_DEFAULT_TIMEOUT,
            },
            default_timeout=GOLANGCI_LINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set golangci-lint-specific options.

        Args:
            timeout: Timeout in seconds (default: 120).
            **kwargs: Additional options.
        """
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(timeout=timeout)
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return the golangci-lint documentation URL for a sub-linter.

        Args:
            code: Sub-linter name (e.g., "errcheck").

        Returns:
            URL to the golangci-lint linter documentation, or None when no
            code is available.
        """
        if code:
            return DocUrlTemplate.GOLANGCI_LINT.format(code=code)
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run ``golangci-lint run`` and parse linting issues.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No Go files found to check.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        module_roots = _find_go_module_roots(ctx.files)
        if not module_roots:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No go.mod found; skipping golangci-lint.",
                issues_count=0,
            )

        cmd = _build_golangci_lint_command(fix=False)

        all_issues: list[GolangciLintIssue] = []
        failure_outputs: list[str] = []
        overall_success = True
        for module_root in module_roots:
            try:
                success_cmd, output = run_subprocess_with_timeout(
                    tool=self,
                    cmd=cmd,
                    timeout=ctx.timeout,
                    cwd=str(module_root),
                    tool_name="golangci_lint",
                )
            except subprocess.TimeoutExpired:
                # A single module timing out must not discard findings already
                # collected from earlier roots or skip the remaining roots.
                # Aggregate the timeout as a failure and continue.
                timeout_result = create_timeout_result(
                    tool=self,
                    timeout=ctx.timeout,
                    cmd=cmd,
                    tool_name="golangci_lint",
                )
                overall_success = False
                all_issues.extend(timeout_result.issues or [])
                if timeout_result.output:
                    failure_outputs.append(timeout_result.output)
                continue

            issues = parse_golangci_lint_output(output=output)
            _rebase_issue_paths(issues=issues, module_root=module_root)
            all_issues.extend(issues)
            overall_success = overall_success and bool(success_cmd)

            # Preserve output when the command fails with no parsed issues so
            # config errors or build failures are visible for debugging.
            if not success_cmd and not issues:
                failure_outputs.append(output)

        return ToolResult(
            name=self.definition.name,
            success=overall_success,
            output="\n".join(failure_outputs) if failure_outputs else None,
            issues_count=len(all_issues),
            issues=all_issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run ``golangci-lint run --fix`` then re-check for remaining issues.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No Go files found to fix.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        module_roots = _find_go_module_roots(ctx.files)
        if not module_roots:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No go.mod found; skipping golangci-lint.",
                issues_count=0,
                initial_issues_count=0,
                fixed_issues_count=0,
                remaining_issues_count=0,
            )

        results = [
            self._fix_module_root(module_root=root, timeout=ctx.timeout)
            for root in module_roots
        ]
        if len(results) == 1:
            return results[0]
        return _merge_fix_results(name=self.definition.name, results=results)

    def _fix_module_root(self, *, module_root: Path, timeout: int) -> ToolResult:
        """Run the check/fix/re-check cycle for a single Go module root.

        Args:
            module_root: Directory containing the module's ``go.mod``.
            timeout: Timeout in seconds for each golangci-lint invocation.

        Returns:
            ToolResult for this module root.
        """
        check_cmd = _build_golangci_lint_command(fix=False)

        # Count issues before fixing.
        try:
            _success_check, output_check = run_subprocess_with_timeout(
                tool=self,
                cmd=check_cmd,
                timeout=timeout,
                cwd=str(module_root),
                tool_name="golangci_lint",
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=timeout,
                cmd=check_cmd,
                tool_name="golangci_lint",
            )
            # The initial check never completed, so no issues were parsed.
            # Report zero remaining issues rather than inventing a phantom
            # finding that would corrupt multi-module fix totals.
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
                issues=timeout_result.issues,
                initial_issues_count=0,
                fixed_issues_count=0,
                remaining_issues_count=0,
            )

        initial_issues = parse_golangci_lint_output(output=output_check)
        _rebase_issue_paths(issues=initial_issues, module_root=module_root)
        initial_count = len(initial_issues)

        # Run fix.
        fix_cmd = _build_golangci_lint_command(fix=True)
        try:
            success_fix, output_fix = run_subprocess_with_timeout(
                tool=self,
                cmd=fix_cmd,
                timeout=timeout,
                cwd=str(module_root),
                tool_name="golangci_lint",
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=timeout,
                cmd=fix_cmd,
                tool_name="golangci_lint",
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=initial_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
                initial_issues=initial_issues if initial_issues else None,
            )

        # Re-check after fix to count remaining issues.
        try:
            success_after, output_after = run_subprocess_with_timeout(
                tool=self,
                cmd=check_cmd,
                timeout=timeout,
                cwd=str(module_root),
                tool_name="golangci_lint",
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=timeout,
                cmd=check_cmd,
                tool_name="golangci_lint",
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=initial_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
                initial_issues=initial_issues if initial_issues else None,
            )

        remaining_issues = parse_golangci_lint_output(output=output_after)
        _rebase_issue_paths(issues=remaining_issues, module_root=module_root)
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

        # golangci-lint exits non-zero both when lint issues remain and when the
        # run itself errors (config/build/fixer failure). A failed --fix run must
        # not be reported as a successful format, and its diagnostic output must
        # never be dropped: gate success on the fix command succeeding and always
        # surface the fix command's output whenever it failed — regardless of how
        # many issues the re-check parsed — so a fixer/config/build error can't be
        # hidden behind a re-check that happens to report remaining issues.
        success = remaining_count == 0 and success_after and success_fix

        if not success_fix:
            output = output_fix or output_after
        elif not success and remaining_count == 0:
            output = output_after
        else:
            output = None

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=output,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues if initial_issues else None,
        )
