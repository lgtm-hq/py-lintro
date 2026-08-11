"""Quick-fix generation for doctor output.

A quick fix is only useful if running it changes something. This module filters
the affected tools down to the ones whose install action can actually execute
in the detected environment, and reports the rest as explicit manual steps
instead of folding them into a command that is known to fail.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from lintro.tools.core.install_hints import has_install_script, is_manual_hint
from lintro.tools.core.install_strategies import InstallEnvironment, get_strategy
from lintro.tools.core.manifest_models import ManifestTool


@dataclass
class QuickFix:
    """An executable remediation command plus the steps it cannot cover.

    Attributes:
        tool_names: Tools the generated command can actually install/upgrade.
        upgrade: Whether the command needs ``--upgrade``.
        blocked: ``(tool name, reason)`` pairs that need manual action.
    """

    tool_names: list[str] = field(default_factory=list)
    upgrade: bool = False
    blocked: list[tuple[str, str]] = field(default_factory=list)

    @property
    def command(self) -> str | None:
        """Render the runnable quick-fix command.

        Returns:
            The ``lintro install`` command, or None when no action in the
            detected environment would make progress.
        """
        if not self.tool_names:
            return None
        upgrade_flag = " --upgrade" if self.upgrade else ""
        return f"lintro install{upgrade_flag} {' '.join(self.tool_names)}"


def build_quick_fix(
    tools: Sequence[ManifestTool],
    env: InstallEnvironment,
    *,
    upgrade: bool = False,
    known_invalid: Collection[str] = (),
) -> QuickFix:
    """Build a quick fix covering only executable actions.

    Args:
        tools: Tools that need installing or upgrading.
        env: The detected install environment.
        upgrade: Whether the affected tools need an upgrade rather than a
            fresh install.
        known_invalid: Tool names whose command already ran without resolving
            the tool in this session; they are reported as blocked rather than
            suggested again.

    Returns:
        QuickFix with an executable command and the blocked remainder.
    """
    quick_fix = QuickFix(upgrade=upgrade)

    for tool in tools:
        if tool.name in known_invalid:
            quick_fix.blocked.append(
                (
                    tool.name,
                    "previous attempt did not resolve it; needs manual action",
                ),
            )
            continue

        strategy = get_strategy(tool.install_type)
        if strategy is None:
            quick_fix.blocked.append(
                (tool.name, f"no install strategy for type {tool.install_type!r}"),
            )
            continue

        reason = strategy.check_prerequisites(env, tool.name)
        if reason:
            quick_fix.blocked.append((tool.name, reason))
            continue

        hint = (
            strategy.upgrade_hint(
                env,
                tool.name,
                tool.version,
                tool.install_package,
                tool.install_component,
            )
            if upgrade
            else strategy.install_hint(
                env,
                tool.name,
                tool.version,
                tool.install_package,
                tool.install_component,
            )
        )
        if is_manual_hint(hint) and not has_install_script(tool):
            quick_fix.blocked.append((tool.name, hint))
            continue

        quick_fix.tool_names.append(tool.name)

    return quick_fix
