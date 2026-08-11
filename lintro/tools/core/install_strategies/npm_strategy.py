"""npm/bun install strategy.

Follows the Node package-manager policy in
:mod:`lintro.tools.core.install_strategies.node_project` (#2005): the manager
comes from explicit user choice, then the project's ``packageManager`` field,
then lockfile evidence, then whatever is installed — and inside a Node project
the tool is added as a **dev dependency**, not to a machine-wide prefix.

This deliberately mirrors runtime resolution (#1811), where a project-local
``node_modules/.bin`` install wins: what ``lintro install`` writes is what
``lintro check`` will run.
"""

from __future__ import annotations

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.brew_names import BREW_FORMULA_NAMES
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.node_project import (
    NODE_MANAGER_COMMANDS,
    NODE_MANAGERS,
    add_dependency_command,
)
from lintro.tools.core.install_strategies.registry import register_strategy


class NpmStrategy(InstallStrategy):
    """Install strategy for npm/bun-managed JavaScript packages."""

    def install_type(self) -> str:
        """Return ``'npm'``."""
        return "npm"

    def is_available(self, env: InstallEnvironment) -> bool:
        """Available if any supported Node package manager, or brew, exists."""
        return _has_node_manager(env) or env.has(PackageManager.BREW)

    def check_prerequisites(
        self,
        env: InstallEnvironment,
        tool_name: str,
    ) -> str | None:
        """Return skip reason if no JS package manager is available.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.

        Returns:
            Skip reason or None.
        """
        if _has_node_manager(env):
            return None
        if env.has(PackageManager.BREW) and tool_name in BREW_FORMULA_NAMES:
            return None
        return "bun/npm not available (install Node.js first)"

    def install_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate npm install hint.

        The pin guard runs here too, not only on the upgrade path. A package
        that the project *declares* but has not installed (a fresh clone, a
        pruned ``node_modules``) reaches planning as **missing**, so without
        this guard lintro would emit ``npm install -D <pkg>@<recommended>`` and
        rewrite the project's pin — the precise failure #2005 exists to prevent,
        arrived at through the install path rather than the upgrade path.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            _install_component: Unused for npm.

        Returns:
            Shell command string, or a manual-action message when the project
            declares a different version.
        """
        pkg = install_package or tool_name
        conflict = _pin_conflict(env, pkg, tool_version, upgrading=False)
        if conflict is not None:
            return conflict
        brew_hint = _brew_hint(env, tool_name, verb="install")
        if brew_hint is not None:
            return brew_hint
        return _node_add_command(env, pkg, tool_version)

    def upgrade_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate npm upgrade hint (npm replaces on install).

        A project-local pin is never silently replaced. When the enclosing
        project declares the package at a spec that does not already admit
        lintro's recommendation, this reports the difference and asks for an
        explicit decision rather than emitting a command that would rewrite the
        project's ``package.json`` behind the user's back. ``ToolInstaller``
        routes such "Upgrade ..." strings to the manual list.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            _install_component: Unused for npm.

        Returns:
            Shell command string, or a manual-action message when the project
            pins a different version.
        """
        pkg = install_package or tool_name
        conflict = _pin_conflict(env, pkg, tool_version, upgrading=True)
        if conflict is not None:
            return conflict
        brew_hint = _brew_hint(env, tool_name, verb="upgrade")
        if brew_hint is not None:
            return brew_hint
        return _node_add_command(env, pkg, tool_version)


def _node_add_command(
    env: InstallEnvironment,
    package: str,
    version: str,
) -> str:
    """Build the manager-appropriate add command for a package.

    Args:
        env: The current install environment.
        package: npm package name.
        version: Version lintro recommends.

    Returns:
        Shell command string.
    """
    manager, _source = env.node_manager()
    return add_dependency_command(
        manager=manager,
        spec=f"{package}@{version}",
        global_install=env.installs_globally(),
    )


def _pin_conflict(
    env: InstallEnvironment,
    package: str,
    version: str,
    *,
    upgrading: bool,
) -> str | None:
    """Report a project-local pin that disagrees with lintro's recommendation.

    Only the *declared* spec is compared, because that is what the project
    author wrote down and what an install or upgrade command would overwrite. A
    spec that already names the recommended version (``3.9.4``, ``^3.9.4``,
    ``~3.9.4``) is not a conflict.

    The advice differs by path. A declared-but-missing package does not need a
    versioned add at all — it needs the project's own dependencies installed,
    which restores the pinned version from the lockfile. Suggesting a versioned
    add there would be suggesting the very rewrite this guard exists to stop.

    Args:
        env: The current install environment.
        package: npm package name.
        version: Version lintro recommends.
        upgrading: Whether the caller is planning an upgrade of an already
            installed tool rather than a first install.

    Returns:
        A manual-action message, or None when there is no conflict.
    """
    project = env.node_project
    if project is None or env.prefer_global:
        return None
    declared = project.declared_spec(package)
    if declared is None or declared.lstrip("^~=v ").strip() == version:
        return None
    manager, _source = env.node_manager()
    adopt = add_dependency_command(
        manager=manager,
        spec=f"{package}@{version}",
        global_install=False,
    )
    difference = (
        f"this project declares {declared} in package.json but lintro "
        f"recommends {version}."
    )
    if upgrading:
        return (
            f"Upgrade {package} explicitly: {difference} "
            f"Run `{adopt}` to adopt lintro's version, or keep the project pin."
        )
    restore = NODE_MANAGER_COMMANDS[manager].install_all
    return (
        f"Install {package} from the project: {difference} "
        f"Run `{restore}` to install the version this project pins, or "
        f"`{adopt}` to adopt lintro's instead."
    )


def _brew_hint(
    env: InstallEnvironment,
    tool_name: str,
    *,
    verb: str,
) -> str | None:
    """Build a Homebrew command when brew is the right authority here.

    Args:
        env: The current install environment.
        tool_name: Canonical tool name.
        verb: ``"install"`` or ``"upgrade"``.

    Returns:
        Shell command string, or None when brew should not be used.
    """
    brew_pkg = BREW_FORMULA_NAMES.get(tool_name)
    if brew_pkg is None or not env.has(PackageManager.BREW):
        return None
    # Inside a Node project the project's own dependency set is the authority,
    # so a machine-wide formula must not pre-empt it (#2005). The exception is a
    # machine with no Node package manager at all: prerequisites pass on brew
    # alone, so without this a project-local `npm install -D` would be planned
    # on a machine that has no npm and fail with a raw OS error.
    if (
        env.node_project is not None
        and not env.prefer_global
        and _has_node_manager(env)
    ):
        return None
    if _is_homebrew_context(env) or not _has_node_manager(env):
        return f"brew {verb} {brew_pkg}"
    return None


def _has_node_manager(env: InstallEnvironment) -> bool:
    """Report whether any supported Node package manager is installed.

    pnpm and yarn count: a pnpm-only machine can install Node tools perfectly
    well, and previously fell through to the "install Node.js first" skip.

    Args:
        env: The current install environment.

    Returns:
        True when at least one Node package manager is on PATH.
    """
    return any(env.has(manager) for manager in NODE_MANAGERS)


def _is_homebrew_context(env: InstallEnvironment) -> bool:
    """Check if the environment is a Homebrew install with brew available."""
    return env.has(PackageManager.BREW) and env.install_context in (
        InstallContext.HOMEBREW_FULL,
        InstallContext.HOMEBREW_BIN,
    )


register_strategy(NpmStrategy())
