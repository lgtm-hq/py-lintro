"""Runtime Python format ownership shared by configuration and reporting."""

from collections.abc import Collection, Mapping
from pathlib import Path

from lintro.enums.action import Action
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


def black_owns_python_formatting(
    selected_tools: Collection[str],
    *,
    cache_root: Path | None = None,
) -> bool:
    """Require execution-gate evidence before turning off Ruff's format stages.

    Args:
        selected_tools: Tools selected for this invocation.
        cache_root: Black's execution directory.

    Returns:
        Whether Black will run under the same policy as the execution gate.
    """
    if "black" not in selected_tools:
        return False
    snapshot = _confirmed_snapshot("black", cache_root)
    return snapshot is not None and tool_snapshots.snapshot_is_runnable(snapshot)


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


def python_formatting_notice(
    selected_tools: Collection[str],
    *,
    action: Action,
    ruff_options: Mapping[str, object],
    cache_root: Path | None = None,
) -> str | None:
    """Describe runtime ownership, explicit overrides, and unconfirmed probes.

    Args:
        selected_tools: Tools selected for this invocation.
        action: Executed check or format action.
        ruff_options: Explicit Ruff options with CLI overrides applied.
        cache_root: Directory anchoring the formatters' execution probes.

    Returns:
        Ownership explanation, or None when the pair is not selected.
    """
    if not {"ruff", "black"}.issubset(selected_tools):
        return None
    black = _confirmed_snapshot("black", cache_root)
    if black is None:
        return (
            "Python formatting owner could not be confirmed (capability probe failed)"
        )
    black_owns = tool_snapshots.snapshot_is_runnable(black)
    option = "format" if action == Action.FIX else "format_check"
    ruff_enabled = bool(ruff_options.get(option, not black_owns))
    if black_owns:
        reason = "available and version gate passed"
        if not black.version_check_passed:
            reason = "version lag allowed by LINTRO_ALLOW_VERSION_LAG"
        suffix = (
            f"ruff {option} explicitly enabled"
            if ruff_enabled
            else f"ruff {option} not run"
        )
        return f"black owns Python formatting ({reason}); {suffix}"
    reason = _unavailable_reason(black)
    if not ruff_enabled:
        return (
            f"Python formatting has no runnable owner ({reason}; "
            f"ruff {option} disabled)"
        )
    ruff = _confirmed_snapshot("ruff", cache_root)
    if ruff is None:
        return (
            "Python formatting owner could not be confirmed (capability probe failed)"
        )
    if tool_snapshots.snapshot_is_runnable(ruff):
        return f"ruff owns Python formatting ({reason})"
    return (
        f"Python formatting has no runnable owner ({reason}; "
        f"{_unavailable_reason(ruff)})"
    )
