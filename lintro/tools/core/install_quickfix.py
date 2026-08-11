"""Quick-fix generation for doctor output.

A quick fix is only useful if running it changes something. This module filters
the affected tools down to the ones whose install action can actually execute
in the detected environment, and reports the rest as explicit manual steps
instead of folding them into a command that is known to fail.

The install-versus-upgrade decision is made per tool, mirroring
``ToolInstaller.plan``: a missing tool is installed, an outdated one upgraded.
A batch-wide flag would suggest ``brew upgrade`` for a tool that is not
installed yet, or hide an installable tool behind another tool's upgrade.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field

from lintro.tools.core.install_hints import has_install_script, is_manual_hint
from lintro.tools.core.install_strategies import InstallEnvironment, get_strategy
from lintro.tools.core.manifest_models import ManifestTool

#: Predicate deciding whether Homebrew manages a formula.
BrewManagedCheck = Callable[[str], bool]


@dataclass
class QuickFix:
    """Executable remediation commands plus the steps they cannot cover.

    Attributes:
        install_names: Tools a fresh ``lintro install`` can handle.
        upgrade_names: Tools that need ``lintro install --upgrade``.
        blocked: ``(tool name, reason)`` pairs that need manual action.
    """

    install_names: list[str] = field(default_factory=list)
    upgrade_names: list[str] = field(default_factory=list)
    blocked: list[tuple[str, str]] = field(default_factory=list)

    @property
    def commands(self) -> list[str]:
        """Render the runnable quick-fix commands.

        Returns:
            One command per needed action kind; empty when nothing in the
            detected environment would make progress.
        """
        commands: list[str] = []
        if self.install_names:
            commands.append(f"lintro install {' '.join(self.install_names)}")
        if self.upgrade_names:
            commands.append(
                f"lintro install --upgrade {' '.join(self.upgrade_names)}",
            )
        return commands


def build_quick_fix(
    candidates: Sequence[tuple[ManifestTool, bool]],
    env: InstallEnvironment,
    *,
    known_invalid: Collection[str] = (),
    is_brew_managed: BrewManagedCheck | None = None,
) -> QuickFix:
    """Build a quick fix covering only executable actions.

    Args:
        candidates: ``(tool, needs_upgrade)`` pairs, where ``needs_upgrade`` is
            True for an installed-but-outdated tool and False for a missing one.
        env: The detected install environment.
        known_invalid: Tool names whose command already ran without resolving
            the tool in this session; they are reported as blocked rather than
            suggested again.
        is_brew_managed: Predicate used to confirm that Homebrew really manages
            a formula before suggesting ``brew upgrade``; defaults to the
            installer's own check. Injectable so tests stay off subprocess.

    Returns:
        QuickFix with executable commands and the blocked remainder.
    """
    if is_brew_managed is None:
        from lintro.tools.core.tool_installer import ToolInstaller

        is_brew_managed = ToolInstaller._is_brew_managed

    quick_fix = QuickFix()

    for tool, needs_upgrade in candidates:
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

        hint_for = strategy.upgrade_hint if needs_upgrade else strategy.install_hint
        hint = hint_for(
            env,
            tool.name,
            tool.version,
            tool.install_package,
            tool.install_component,
        )

        if is_manual_hint(hint) and not has_install_script(tool):
            quick_fix.blocked.append((tool.name, hint))
            continue

        # Same guard as ToolInstaller._get_install_command: brew can only
        # upgrade what it installed.
        if hint.startswith("brew upgrade"):
            formula = hint.split()[-1]
            if not is_brew_managed(formula):
                quick_fix.blocked.append(
                    (tool.name, f"{formula} is not managed by Homebrew"),
                )
                continue

        if needs_upgrade:
            quick_fix.upgrade_names.append(tool.name)
        else:
            quick_fix.install_names.append(tool.name)

    return quick_fix
