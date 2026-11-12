"""Runtime Python format ownership shared by configuration and reporting."""

from collections.abc import Collection, Mapping
from pathlib import Path

from lintro.enums.action import Action
from lintro.models.core.python_formatting_decision import PythonFormattingDecision
from lintro.models.core.tool_outcome import Skipped, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.utils import tool_snapshots


def _unavailable_reason(snapshot: ToolSnapshot) -> str:
    """Explain the actual capability failure rather than a list of possibilities.

    Args:
        snapshot: A tool that cannot pass the execution gate.

    Returns:
        Actionable reason naming the tool and its version when known.
    """
    if not snapshot.available:
        return f"{snapshot.name}: {snapshot.probe_error or 'not installed'}"
    if snapshot.version is not None:
        return (
            f"{snapshot.name}: below minimum {snapshot.min_version}, "
            f"found {snapshot.version}"
        )
    return f"{snapshot.name}: {snapshot.probe_error or 'version probe failed'}"


def _resolve_python_formatting(
    selected_tools: Collection[str],
    *,
    action: Action,
    ruff_options: Mapping[str, object],
    cache_root: Path | None = None,
    strict_missing_tools: bool | None = None,
) -> PythonFormattingDecision | None:
    """Resolve ownership once, without discovering files or updating their caches.

    Args:
        selected_tools: Tools selected for this invocation.
        action: Executed check or format action.
        ruff_options: Explicit Ruff options with CLI overrides applied.
        cache_root: Directory anchoring the formatters' execution probes.
        strict_missing_tools: Missing-tool policy for the canonical unavailable result.

    Returns:
        Immutable execution decision, or None when the pair is not selected.
    """
    if not {"ruff", "black"}.issubset(selected_tools):
        return None
    black = tool_snapshots.get_tool_snapshot("black", cache_root=cache_root)
    black_runs = tool_snapshots.snapshot_is_runnable(black)
    ruff_format = bool(ruff_options.get("format", not black_runs))
    ruff_check = bool(ruff_options.get("format_check", not black_runs))
    option = "format" if action == Action.FIX else "format_check"
    stage_enabled = ruff_format if action == Action.FIX else ruff_check
    owner, reason = _format_owner(black, black_runs, stage_enabled, cache_root)
    text = _decision_text(owner, reason, option, stage_enabled, black_runs)
    return PythonFormattingDecision(
        owner=owner,
        reason=reason,
        ruff_format=ruff_format,
        ruff_format_check=ruff_check,
        run_black=black_runs,
        black_result=_blocked_black_result(
            black,
            black_runs,
            reason,
            strict_missing_tools,
        ),
        text=text,
    )


def _format_owner(
    black: ToolSnapshot,
    black_runs: bool,
    ruff_enabled: bool,
    cache_root: Path | None,
) -> tuple[str | None, str]:
    """Confirm the owner from the evidence captured for this invocation.

    Args:
        black: Black capability evidence, or an unconfirmed probe.
        black_runs: Runnability already resolved from the captured evidence.
        ruff_enabled: Whether any Ruff formatting stage is enabled.
        cache_root: Execution directory for Ruff's fallback probe.

    Returns:
        Confirmed owner and the actual diagnosis.
    """
    if black_runs:
        reason = "available and version gate passed"
        if not black.version_check_passed:
            reason = "version lag allowed by LINTRO_ALLOW_VERSION_LAG"
        return "black", reason
    reason = _unavailable_reason(black)
    if not ruff_enabled:
        return None, reason
    ruff = tool_snapshots.get_tool_snapshot("ruff", cache_root=cache_root)
    if tool_snapshots.snapshot_is_runnable(ruff):
        return "ruff", reason
    return None, f"{reason}; {_unavailable_reason(ruff)}"


def _decision_text(
    owner: str | None,
    reason: str,
    option: str,
    stage_enabled: bool,
    black_runs: bool,
    probe_failed: bool = False,
) -> str:
    """Render recorded policy without another capability probe.

    Args:
        owner: Confirmed owner, when known.
        reason: Recorded capability diagnosis.
        option: Ruff stage relevant to the requested action.
        stage_enabled: Whether that stage is enabled.
        black_runs: Whether Black is permitted to run.
        probe_failed: Whether ownership resolution raised an internal exception.

    Returns:
        Human-readable ownership and stage state.
    """
    if black_runs:
        suffix = (
            f"ruff {option} explicitly enabled"
            if stage_enabled
            else f"ruff {option} not run"
        )
        return f"black owns Python formatting ({reason}); {suffix}"
    if probe_failed:
        state = "enabled" if stage_enabled else "disabled"
        return (
            f"Python formatting owner could not be confirmed ({reason}); "
            f"ruff {option} {state}; black not run after probe error"
        )
    if owner == "ruff" and stage_enabled:
        return f"ruff owns Python formatting ({reason})"
    state = "unavailable" if stage_enabled else "disabled"
    return (
        f"Python formatting has no runnable owner ({reason}; " f"ruff {option} {state})"
    )


def _blocked_black_result(
    snapshot: ToolSnapshot,
    black_runs: bool,
    reason: str,
    strict: bool | None,
) -> ToolResult | None:
    """Preserve the native gate's result and remediation for blocked Black.

    Args:
        snapshot: Captured capability evidence.
        black_runs: Runnability already captured for this invocation.
        reason: Recorded version-gate diagnosis.
        strict: Missing-tool policy, or None to read configuration.

    Returns:
        The canonical unavailable result, a version skip, or None when runnable.
    """
    if not snapshot.available:
        return tool_snapshots.snapshot_to_unavailable_result(snapshot, strict=strict)
    if black_runs:
        return None
    return ToolResult.from_outcome(
        ToolOutcome(execution=Skipped(reason)),
        name="black",
        output=f"Skipping black: {reason}",
    )


def resolve_python_formatting(
    selected_tools: Collection[str],
    *,
    action: Action,
    ruff_options: Mapping[str, object],
    cache_root: Path | None = None,
    strict_missing_tools: bool | None = None,
) -> PythonFormattingDecision | None:
    """Contain ownership-resolution errors as failing Black results for every caller.

    Args:
        selected_tools: Tools selected for this invocation.
        action: Executed check or format action.
        ruff_options: Explicit Ruff options with CLI overrides applied.
        cache_root: Directory anchoring execution probes.
        strict_missing_tools: Missing-tool policy for canonical unavailable results.

    Returns:
        A frozen policy retaining any internal failure, never a successful error skip.
    """
    if not {"ruff", "black"}.issubset(selected_tools):
        return None
    try:
        return _resolve_python_formatting(
            selected_tools,
            action=action,
            ruff_options=ruff_options,
            cache_root=cache_root,
            strict_missing_tools=strict_missing_tools,
        )
    except Exception as error:
        # This is the shared per-tool exception boundary: all entry points must
        # preserve the failure while allowing Ruff and other tools to continue.
        detail = f"{type(error).__name__}: {error}"
        reason = f"capability probe failed: {detail}"
        ruff_format = bool(ruff_options.get("format", True))
        ruff_check = bool(ruff_options.get("format_check", True))
        return PythonFormattingDecision(
            owner=None,
            reason=reason,
            ruff_format=ruff_format,
            ruff_format_check=ruff_check,
            run_black=False,
            black_result=ToolResult(
                name="black",
                success=False,
                execution_failed=True,
                output=f"Tool execution failed: {detail}",
                inspected_files=False,
                residual_unknown=action == Action.FIX,
                residual_unknown_reason=reason if action == Action.FIX else None,
            ),
            text=_decision_text(
                None,
                reason,
                "format" if action == Action.FIX else "format_check",
                ruff_format if action == Action.FIX else ruff_check,
                False,
                probe_failed=True,
            ),
        )
