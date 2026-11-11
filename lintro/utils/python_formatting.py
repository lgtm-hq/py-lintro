"""Runtime Python format ownership shared by configuration and reporting."""

from collections.abc import Collection, Mapping
from pathlib import Path

from lintro.enums.action import Action
from lintro.models.core.python_formatting_decision import PythonFormattingDecision
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.utils import tool_snapshots


def _confirmed_snapshot(name: str, cache_root: Path | None) -> ToolSnapshot | None:
    """Read probe evidence without turning an unconfirmed probe into a demotion.

    Args:
        name: Tool to probe.
        cache_root: Tool execution directory.

    Returns:
        Evidence, or None when the probe service or its I/O failed.
    """
    try:
        return tool_snapshots.get_tool_snapshot(name, cache_root=cache_root)
    except (OSError, RuntimeError):
        return None


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


def resolve_python_formatting(
    selected_tools: Collection[str],
    *,
    action: Action,
    ruff_options: Mapping[str, object],
    cache_root: Path | None = None,
) -> PythonFormattingDecision | None:
    """Resolve ownership once, without discovering files or updating their caches.

    Args:
        selected_tools: Tools selected for this invocation.
        action: Executed check or format action.
        ruff_options: Explicit Ruff options with CLI overrides applied.
        cache_root: Directory anchoring the formatters' execution probes.

    Returns:
        Immutable execution decision, or None when the pair is not selected.
    """
    if not {"ruff", "black"}.issubset(selected_tools):
        return None
    black = _confirmed_snapshot("black", cache_root)
    black_runs = black is not None and tool_snapshots.snapshot_is_runnable(black)
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
        black_unavailable=black is not None and not black.available,
        text=text,
    )


def _format_owner(
    black: ToolSnapshot | None,
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
    if black is None:
        return None, "capability probe failed"
    if black_runs:
        reason = "available and version gate passed"
        if not black.version_check_passed:
            reason = "version lag allowed by LINTRO_ALLOW_VERSION_LAG"
        return "black", reason
    reason = _unavailable_reason(black)
    if not ruff_enabled:
        return None, reason
    ruff = _confirmed_snapshot("ruff", cache_root)
    if ruff is None:
        return None, "capability probe failed"
    if tool_snapshots.snapshot_is_runnable(ruff):
        return "ruff", reason
    return None, f"{reason}; {_unavailable_reason(ruff)}"


def _decision_text(
    owner: str | None,
    reason: str,
    option: str,
    stage_enabled: bool,
    black_runs: bool,
) -> str:
    """Render recorded policy without another capability probe.

    Args:
        owner: Confirmed owner, when known.
        reason: Recorded capability diagnosis.
        option: Ruff stage relevant to the requested action.
        stage_enabled: Whether that stage is enabled.
        black_runs: Whether Black is permitted to run.

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
    if reason == "capability probe failed":
        state = "enabled" if stage_enabled else "disabled"
        return (
            f"Python formatting owner could not be confirmed ({reason}); "
            f"ruff {option} {state}; black skipped for this run"
        )
    if owner == "ruff" and stage_enabled:
        return f"ruff owns Python formatting ({reason})"
    state = "unavailable" if stage_enabled else "disabled"
    return (
        f"Python formatting has no runnable owner ({reason}; " f"ruff {option} {state})"
    )
