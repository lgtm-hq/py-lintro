"""Clippy tool definition.

Clippy is Rust's official linter with hundreds of lint rules for correctness,
style, complexity, and performance. It runs via `cargo clippy` and requires
a Cargo.toml file in the project.
"""

# mypy: ignore-errors
# Note: mypy errors are suppressed because lintro runs mypy from file's directory,
# breaking package resolution. When run properly (mypy lintro/...), this file passes.

from __future__ import annotations

import re
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.tool_outcome import (
    Checked,
    FixMeasured,
    FixUnmeasured,
    NoFiles,
    Skipped,
    TimedOut,
    ToolOutcome,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.clippy.clippy_issue import ClippyIssue
from lintro.parsers.clippy.clippy_parser import parse_clippy_diagnostics
from lintro.plugins.base import BaseToolPlugin, PreparedExecution
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.cargo import cargo_package_args, resolve_cargo_root
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
)
from lintro.tools.core.timeout_utils import (
    run_subprocess_with_timeout,
)

# Constants for Clippy configuration
CLIPPY_DEFAULT_TIMEOUT: int = 120
CLIPPY_FILE_PATTERNS: list[str] = ["*.rs", "Cargo.toml"]


@dataclass
class _ClippyRun:
    """Native verdict and independent diagnostic measurements for one pass.

    Attributes:
        success: Whether Cargo exited zero.
        output: Complete native output, including configuration errors.
        issues: Usable Clippy diagnostics.
        parse_failures: Number of malformed records.
        compiler_errors: Number of hard compiler errors, excluding denied lints.
        lint_diagnostics: Number of Clippy or rustc lint verdicts.
        cargo_failed: Whether plain-text Cargo errors indicate an incomplete pass.
        timed_out: Whether the pass stopped before measurement completed.
    """

    success: bool
    output: str
    issues: list[ClippyIssue]
    parse_failures: int = 0
    compiler_errors: int = 0
    lint_diagnostics: int = 0
    cargo_failed: bool = False
    timed_out: bool = False

    @property
    def execution_failed(self) -> bool:
        """Identify native failures independently of parser failures.

        Returns:
            Whether hard compiler errors or an independent Cargo failure occurred.
        """
        return bool(self.compiler_errors) or (
            not self.success
            and (self.cargo_failed or not (self.lint_diagnostics or self.issues))
        )


def _cargo_failure(output: str) -> bool:
    """Find plain-text Cargo errors independently of JSON lint verdicts.

    Args:
        output: Combined Cargo output, including progress and diagnostic JSON.

    Returns:
        Whether an error other than Cargo's redundant compile summary appears.
    """
    return any(
        line.startswith(("error:", "error["))
        and not re.fullmatch(
            r"error: could not compile `[^`]+` .+ due to "
            r"(?:\d+ )?previous errors?(?:; .*)?",
            line,
        )
        for line in strip_ansi_codes(output).splitlines()
    )


def _build_clippy_command(
    fix: bool = False,
    selection: list[str] | None = None,
) -> list[str]:
    """Build the cargo clippy command.

    Args:
        fix: Whether to include --fix flag.
        selection: Package-selection arguments from
            ``lintro.tools.core.cargo.cargo_package_args``. Without them a
            workspace root that sets ``default-members`` would lint only
            those members, so a touched crate outside the set would report
            clean.

    Returns:
        List of command arguments.
    """
    cmd = [
        "cargo",
        "clippy",
        *(selection or []),
        "--all-targets",
        "--all-features",
        "--message-format=json",
    ]
    if fix:
        cmd.extend(["--fix", "--allow-dirty", "--allow-staged"])
    return cmd


@register_tool
@dataclass
class ClippyPlugin(BaseToolPlugin):
    """Clippy Rust linter plugin.

    This plugin integrates Rust's Clippy linter with Lintro for checking
    Rust code for correctness, style, and performance issues.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="clippy",
            description=("Rust linter for correctness, style, and performance"),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=CLIPPY_FILE_PATTERNS,
            claims=[
                Claim(
                    patterns=CLIPPY_FILE_PATTERNS,
                    capabilities={Cap.FIX, Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=False,
            native_configs=["clippy.toml", ".clippy.toml"],
            version_command=["rustc", "--version"],
            min_version=get_min_version(ToolName.CLIPPY),
            default_options={
                "timeout": CLIPPY_DEFAULT_TIMEOUT,
            },
            default_timeout=CLIPPY_DEFAULT_TIMEOUT,
        )

    def _verify_tool_version(
        self,
        cwd: str | Path | None = None,
    ) -> ToolResult | None:
        """Verify that Rust toolchain meets minimum version requirements.

        Clippy version is tied to Rust version, so we check rustc version instead.

        Args:
            cwd: Directory clippy will run in; a ``rust-toolchain`` file there
                selects the rustc that answers.

        Returns:
            Optional[ToolResult]: None if version check passes, or a skip result
                if it fails.
        """
        from lintro.tools.core.version_requirements import check_tool_version

        # Check Rust version instead of clippy version
        version_info = check_tool_version("clippy", ["rustc"], cwd=cwd)

        if version_info.version_check_passed:
            return None  # Version check passed

        # Version check failed - return skip result with warning
        skip_message = (
            f"Skipping {self.definition.name}: {version_info.error_message}. "
            f"Minimum required: {version_info.min_version}. "
            f"{version_info.install_hint}"
        )

        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Skipped(
                    version_info.error_message or "Clippy version check failed",
                ),
            ),
            name=self.definition.name,
            output=skip_message,
        )

    def set_options(
        self,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Clippy-specific options.

        Args:
            timeout: Timeout in seconds (default: 120).
            **kwargs: Additional options.
        """
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(timeout=timeout)
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return Clippy documentation URL for the given lint name.

        Args:
            code: Clippy lint name (e.g., "needless_return").

        Returns:
            URL to the Clippy lint documentation.
        """
        if code:
            return DocUrlTemplate.CLIPPY.format(code=code)
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run `cargo clippy` and parse linting issues.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self.prepare(
            paths,
            options,
            no_files_message="No Rust files found to check.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        resolved = resolve_cargo_root(ctx.files, tool_label="clippy")
        cargo_root = resolved.root
        if cargo_root is None:
            return ToolResult.from_outcome(
                ToolOutcome(execution=NoFiles()),
                name=self.definition.name,
                output=resolved.skip_message("clippy"),
            )

        cmd = _build_clippy_command(
            fix=False,
            selection=cargo_package_args(ctx.files, cargo_root),
        )

        run = self._run_clippy(cmd, ctx.timeout, str(cargo_root))
        execution = (
            TimedOut(run.output)
            if run.timed_out
            else Checked(
                success=run.success
                and not run.parse_failures
                and not run.execution_failed,
                inspected_files=(
                    True if not (run.parse_failures or run.execution_failed) else None
                ),
            )
        )
        return ToolResult.from_outcome(
            ToolOutcome(execution=execution, issues_count=len(run.issues)),
            name=self.definition.name,
            issues=run.issues,
            parse_failures_count=run.parse_failures or None,
            execution_failed=run.execution_failed,
            # Rustc lint verdicts are absent from structured Clippy findings.
            output=(
                run.output
                if run.timed_out
                or run.parse_failures
                or run.execution_failed
                or (
                    not run.success
                    and (not run.issues or run.lint_diagnostics > len(run.issues))
                )
                else None
            ),
        )

    def _run_clippy(self, cmd: list[str], timeout: float, cwd: str) -> _ClippyRun:
        """Execute and parse one pass without losing independent failure evidence.

        Args:
            cmd: Complete Cargo invocation.
            timeout: Deadline for the pass.
            cwd: Cargo project root.

        Returns:
            Native verdict, usable findings and parser/compiler statistics.
        """
        timed_out = False
        try:
            success, output = run_subprocess_with_timeout(
                tool=self,
                cmd=cmd,
                timeout=timeout,
                cwd=cwd,
                tool_name="clippy",
            )
        except subprocess.TimeoutExpired as exc:
            success, timed_out = False, True
            # The shared timeout wrapper keeps native output in its cause.
            cause = exc.__cause__
            partial = (
                (cause.output if isinstance(cause, subprocess.TimeoutExpired) else None)
                or exc.output
                or str(exc)
            )
            output = (
                partial.decode(errors="replace")
                if isinstance(partial, bytes)
                else partial
            )
        diagnostics = parse_clippy_diagnostics(output)
        return _ClippyRun(
            success,
            output,
            diagnostics.issues,
            diagnostics.failures,
            diagnostics.compiler_errors,
            diagnostics.lint_diagnostics,
            _cargo_failure(output),
            timed_out=timed_out,
        )

    def _run_fix_stage(
        self,
        cmd: list[str],
        ctx: PreparedExecution,
        cwd: str,
        initial_issues: list[ClippyIssue] | None = None,
    ) -> _ClippyRun | ToolResult:
        """Stop incomplete fix passes without inventing before/after counts.

        Args:
            cmd: Complete check or mutation command.
            ctx: Prepared execution context.
            cwd: Cargo project root.
            initial_issues: Findings from a completed initial check, if measured.

        Returns:
            A complete pass or an unmeasured failure with native evidence.
        """
        run = self._run_clippy(cmd, ctx.timeout, cwd)
        if not (run.parse_failures or run.execution_failed or run.timed_out):
            return run
        retained = run.issues or initial_issues or []
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=(TimedOut(run.output) if run.timed_out else Checked(False)),
                accounting=FixUnmeasured(
                    initial=(
                        len(initial_issues) if initial_issues is not None else None
                    ),
                    reason="Clippy pass incomplete; fix counts unavailable",
                ),
                issues_count=len(retained),
            ),
            name=self.definition.name,
            issues=retained,
            initial_issues=(
                initial_issues if initial_issues is not None else run.issues
            ),
            output=run.output,
            parse_failures_count=run.parse_failures or None,
            execution_failed=run.execution_failed,
            cwd=cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run `cargo clippy --fix` then re-check for remaining issues.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self.prepare(
            paths,
            options,
            no_files_message="No Rust files found to fix.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        resolved = resolve_cargo_root(ctx.files, tool_label="clippy")
        cargo_root = resolved.root
        if cargo_root is None:
            return ToolResult.from_outcome(
                ToolOutcome(
                    execution=NoFiles(),
                    accounting=FixMeasured(initial=0, fixed=0, remaining=0),
                ),
                name=self.definition.name,
                output=resolved.skip_message("clippy"),
            )

        selection = cargo_package_args(ctx.files, cargo_root)
        check_cmd = _build_clippy_command(fix=False, selection=selection)

        initial = self._run_fix_stage(check_cmd, ctx, str(cargo_root))
        if isinstance(initial, ToolResult):
            return initial

        fix_cmd = _build_clippy_command(fix=True, selection=selection)
        mutation = self._run_fix_stage(fix_cmd, ctx, str(cargo_root), initial.issues)
        if isinstance(mutation, ToolResult):
            return mutation

        after = self._run_fix_stage(check_cmd, ctx, str(cargo_root), initial.issues)
        if isinstance(after, ToolResult):
            return after

        remaining_count = len(after.issues)
        fixed_count = max(0, len(initial.issues) - remaining_count)
        # Recompiling after fixes can surface additional warnings.
        initial_count = max(len(initial.issues), fixed_count + remaining_count)
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(
                    success=remaining_count == 0 and mutation.success and after.success,
                    inspected_files=True,
                ),
                accounting=FixMeasured(
                    initial=initial_count,
                    fixed=fixed_count,
                    remaining=remaining_count,
                ),
                issues_count=remaining_count,
            ),
            name=self.definition.name,
            issues=after.issues,
            initial_issues=initial.issues,
            # Rustc lints are not Clippy issues. Keep failed-stage diagnostics
            # that the structured findings cannot explain, even with zero counts.
            output="\n".join(
                run.output
                for run in (mutation, after)
                if not run.success
                and (not run.issues or run.lint_diagnostics > len(run.issues))
            )
            or None,
            # The verify pass resolves crate-relative diagnostic paths here.
            cwd=str(cargo_root),
        )
