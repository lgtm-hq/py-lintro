"""Runtime Python format ownership shared by configuration and reporting."""

from collections.abc import Collection, Mapping

from lintro.enums.action import Action
from lintro.utils import tool_snapshots


def black_owns_python_formatting(selected_tools: Collection[str]) -> bool:
    """Require runnable Black before turning off Ruff's formatting stages.

    Args:
        selected_tools: Tools selected for this invocation.

    Returns:
        Whether selected Black has a resolved binary and passes its version gate.
    """
    if "black" not in selected_tools:
        return False
    snapshot = tool_snapshots.get_tool_snapshot("black")
    return bool(
        snapshot.available and snapshot.binary_path and snapshot.version_check_passed,
    )


def _python_formatting_notice(
    selected_tools: Collection[str],
    *,
    action: Action,
    ruff_options: Mapping[str, object],
) -> str | None:
    """Describe the Python formatting policy, including explicit overrides.

    Args:
        selected_tools: Tools selected for this invocation.
        action: Executed action, including check for dry-run previews.
        ruff_options: Explicit Ruff options from config and CLI, CLI winning.

    Returns:
        Ownership explanation, or None when the pair is not selected.
    """
    if not {"ruff", "black"}.issubset(selected_tools):
        return None
    black_owns = black_owns_python_formatting(selected_tools)
    option = "format" if action == Action.FIX else "format_check"
    ruff_enabled = "ruff" in selected_tools and bool(
        ruff_options.get(option, not black_owns),
    )
    if black_owns:
        suffix = (
            f"ruff {option} explicitly enabled"
            if ruff_enabled
            else f"ruff {option} not run"
        )
        return (
            "black owns Python formatting (available and version gate passed); "
            f"{suffix}"
        )
    reason = "black unavailable or failed its version gate"
    if ruff_enabled:
        snapshot = tool_snapshots.get_tool_snapshot("ruff")
        if snapshot.available and snapshot.version_check_passed:
            return f"ruff owns Python formatting ({reason})"
    return (
        f"Python formatting has no runnable owner ({reason}; "
        f"ruff {option} unavailable or disabled)"
    )


def python_formatting_notice(
    selected_tools: Collection[str],
    *,
    action: Action,
    ruff_options: Mapping[str, object],
) -> str | None:
    """Report ownership without re-raising an execution-phase probe failure.

    Args:
        selected_tools: Tools selected for this invocation.
        action: Executed check or format action.
        ruff_options: Explicit Ruff options with CLI overrides applied.

    Returns:
        Ownership explanation, or a diagnostic if capability evidence failed.
    """
    try:
        return _python_formatting_notice(
            selected_tools,
            action=action,
            ruff_options=ruff_options,
        )
    except (AttributeError, KeyError, OSError, RuntimeError, ValueError):
        return (
            "Python formatting owner could not be confirmed (capability probe failed)"
        )
