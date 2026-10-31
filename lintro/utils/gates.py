"""Run-level gates that judge a whole run rather than a single file.

Two gates live here: the warn-level module-size gate (#1052) and the
duplicate-code ratchet (#2293). Both need the run's results or its full path
set, so they run once after the primary tools have finished.

This module used to also host ``[tool.lintro.post_checks]``, a second
execution pass whose only real job was running black after ruff. #1742 made
the claims-derived DAG authoritative, so ``ruff -> black`` falls out of the
model and that pass — along with its config table — is gone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat, normalize_output_format
from lintro.models.core.tool_result import ToolResult
from lintro.utils.config import (
    load_lintro_tool_config,
    load_module_size_config,
)
from lintro.utils.duplicate_code import (
    DUPLICATE_CODE_GATE_NAME,
    PYLINT_TOOL_NAME,
    apply_duplicate_code_baseline,
    resolve_duplicate_code_baseline,
)
from lintro.utils.module_size import (
    ModuleSizeReadFailure,
    find_oversized_modules,
    resolve_module_size_settings,
)

if TYPE_CHECKING:
    from lintro.utils.console import ThreadSafeConsoleLogger


def _run_module_size_gate(
    *,
    paths: list[str],
    exclude: str | None,
    include_venv: bool,
    json_output_mode: bool,
    logger: ThreadSafeConsoleLogger,
) -> ToolResult | None:
    """Warn about oversized modules and report unmeasurable files as failures.

    Measured size violations only warn. Read failures produce a failing gate
    result so prior findings and formatting counts can still be rendered. See
    ``lintro/utils/module_size.py`` for the ratchet-down plan.

    Args:
        paths: Paths being linted.
        exclude: Additional comma-separated exclude patterns from the CLI.
        include_venv: Whether virtual-environment directories are included.
        json_output_mode: Whether output is JSON (suppresses console warnings).
        logger: Logger instance for console output.

    Returns:
        ToolResult | None: A failed result for read errors, otherwise None.
    """
    cfg = load_module_size_config()
    if not bool(cfg.get("enabled", True)):
        return None

    threshold, baseline, base_excludes = resolve_module_size_settings(config=cfg)
    exclude_patterns: list[str] = list(base_excludes)
    if exclude:
        exclude_patterns.extend(p.strip() for p in exclude.split(",") if p.strip())

    read_failures: list[ModuleSizeReadFailure] = []
    violations = find_oversized_modules(
        paths=paths,
        threshold=threshold,
        baseline=baseline,
        exclude_patterns=tuple(exclude_patterns),
        include_venv=include_venv,
        read_failures=read_failures,
    )

    if violations and not json_output_mode:
        logger.console_output(
            text=(
                f"Warning: {len(violations)} module(s) exceed the "
                f"{threshold}-line size limit (warn-level, non-blocking):"
            ),
            color="yellow",
        )
        for module in violations:
            logger.console_output(
                text=f"  {module.path} ({module.line_count} lines)",
                color="yellow",
            )
        logger.console_output(text="")

    if not read_failures:
        return None
    message = "\n".join(
        f"Cannot measure module size for {failure.path}: {failure.error}"
        for failure in read_failures
    )
    if not json_output_mode:
        logger.console_output(text=message, color="red")
    return ToolResult(
        name="module-size",
        success=False,
        output=message,
        issues_count=len(read_failures),
    )


def _run_duplicate_code_gate(
    *,
    all_results: list[ToolResult],
    total_issues: int,
    json_output_mode: bool,
    logger: ThreadSafeConsoleLogger,
) -> int:
    """Run the duplicate-code ratchet gate over the run's pylint results.

    Reads ``duplicate_code_baseline`` from ``[tool.lintro.pylint]``, removes the
    ``R0801`` findings from the pylint result so this gate is their only
    accounting, and appends a failing result when the count is above the
    baseline. See ``lintro/utils/duplicate_code.py``.

    Args:
        all_results: Results collected during the run. Appended to, and the
            pylint result is stripped of its duplicate-code findings.
        total_issues: Current total issues count.
        json_output_mode: Whether output is JSON (suppresses console notes).
        logger: Logger instance for console output.

    Returns:
        int: The updated total issues count.
    """
    pylint_config = load_lintro_tool_config(PYLINT_TOOL_NAME)
    baseline = resolve_duplicate_code_baseline(config=pylint_config)
    if baseline is None:
        return total_issues

    verdict = apply_duplicate_code_baseline(results=all_results, baseline=baseline)
    if verdict is None:
        return total_issues

    total_issues = max(total_issues - verdict.count, 0)
    if not verdict.exceeded:
        if not json_output_mode:
            logger.console_output(text=verdict.message, color="green")
        return total_issues

    all_results.append(
        ToolResult(
            name=DUPLICATE_CODE_GATE_NAME,
            success=False,
            output=verdict.message,
            issues_count=1,
        ),
    )
    if not json_output_mode:
        logger.console_output(text=verdict.message, color="red")
    return total_issues + 1


def execute_gates(
    *,
    action: Action,
    paths: list[str],
    exclude: str | None,
    include_venv: bool,
    output_format: str,
    logger: ThreadSafeConsoleLogger,
    all_results: list[ToolResult],
    total_issues: int,
) -> int:
    """Run the run-level gates after the primary tools have finished.

    Args:
        action: The action being performed.
        paths: List of paths that were checked.
        exclude: Patterns to exclude.
        include_venv: Whether virtual environments are included.
        output_format: Output format for results.
        logger: Logger instance for output.
        all_results: Results collected during the run. Appended to by gates
            that fail the run.
        total_issues: Current total issues count.

    Returns:
        int: The updated total issues count.
    """
    # Skip gates for the test action - test is independent from linting.
    if action == Action.TEST:
        return total_issues

    output_fmt_enum: OutputFormat = normalize_output_format(output_format)
    json_output_mode = output_fmt_enum == OutputFormat.JSON

    # Size violations warn (#1052); unreadable modules fail the gate (#3016).
    module_size_result = _run_module_size_gate(
        paths=paths,
        exclude=exclude,
        include_venv=include_venv,
        json_output_mode=json_output_mode,
        logger=logger,
    )
    if module_size_result is not None:
        all_results.append(module_size_result)
        total_issues += module_size_result.issues_count

    # Duplicate-code ratchet gate (issue #2293). Runs after the primary tools
    # so it can judge the pylint result the run already produced.
    return _run_duplicate_code_gate(
        all_results=all_results,
        total_issues=total_issues,
        json_output_mode=json_output_mode,
        logger=logger,
    )
