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

from pathlib import Path
from typing import TYPE_CHECKING

from lintro.enums.declared_spec_kind import DeclaredSpecKind
from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.declared_range import (
    range_admits_at_least,
)
from lintro.tools.core.install_strategies.declared_spec import (
    classify_declared_spec,
    describe_opaque_spec,
    exact_version,
)
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.node_project import (
    NODE_MANAGER_COMMANDS,
    NODE_MANAGERS,
    add_dependency_command,
)
from lintro.tools.core.install_strategies.package_names import (
    BREW_FORMULA_NAMES,
    ecosystem_package_name,
)
from lintro.tools.core.install_strategies.registry import register_strategy

if TYPE_CHECKING:
    from lintro.tools.core.manifest_models import ManifestTool


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

    def tool_hint(
        self,
        env: InstallEnvironment,
        tool: ManifestTool,
        *,
        upgrade: bool,
    ) -> str:
        """Generate the install or upgrade command, honouring ``min_version``.

        A declared range is only worth a project install when it can resolve
        to a version lintro accepts, which is the manifest minimum, not the
        recommendation (#2045).

        Args:
            env: The current install environment.
            tool: Manifest entry of the tool.
            upgrade: Whether to plan an upgrade rather than an install.

        Returns:
            Shell command string, or a manual-action message.
        """
        hint = self.upgrade_hint if upgrade else self.install_hint
        return hint(
            env,
            tool.name,
            tool.version,
            tool.install_package,
            tool.install_component,
            min_version=tool.min_version,
        )

    def install_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        _install_component: str | None,
        *,
        min_version: str | None = None,
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
            min_version: Lowest version lintro accepts; a declared range
                must be able to reach it. Defaults to *tool_version*.

        Returns:
            Shell command string, the manager's install-all command when a
            matching declaration should be restored from the lockfile, or a
            manual-action message when the project declares a different
            version.
        """
        pkg = ecosystem_package_name(tool_name, install_package)
        brew_hint = _brew_hint(env, tool_name, verb="install")
        if brew_hint is not None:
            return brew_hint
        conflict = _pin_conflict(
            env,
            pkg,
            tool_version,
            minimum=min_version or tool_version,
            upgrading=False,
        )
        if conflict is not None:
            return conflict
        return _node_add_command(env, pkg, tool_version, upgrading=False)

    def upgrade_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        _install_component: str | None,
        *,
        min_version: str | None = None,
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
            min_version: Lowest version lintro accepts; a declared range
                must be able to reach it. Defaults to *tool_version*.

        Returns:
            Shell command string, or a manual-action message when the project
            pins a different version.
        """
        pkg = ecosystem_package_name(tool_name, install_package)
        brew_hint = _brew_hint(env, tool_name, verb="upgrade")
        if brew_hint is not None:
            return brew_hint
        conflict = _pin_conflict(
            env,
            pkg,
            tool_version,
            minimum=min_version or tool_version,
            upgrading=True,
        )
        if conflict is not None:
            return conflict
        return _node_add_command(env, pkg, tool_version, upgrading=True)


def _node_add_command(
    env: InstallEnvironment,
    package: str,
    version: str,
    *,
    upgrading: bool,
) -> str:
    """Build the manager-appropriate add command for a package.

    Yarn Berry (2 and later) removed ``yarn global``, so a global add on a
    Berry project gets a manual hint instead of a command that fails with
    "The 'yarn global' commands have been removed in 2.x" (#2045). ``yarn
    dlx`` runs a package once; it does not install one, so it is not offered
    as a substitute.

    Args:
        env: The current install environment.
        package: npm package name.
        version: Version lintro recommends.
        upgrading: Whether the caller is planning an upgrade rather than a
            first install. Only changes the wording of a manual hint.

    Returns:
        Shell command string, or a manual-action message.
    """
    manager, _source = env.node_manager()
    spec = f"{package}@{version}"
    global_install = env.installs_globally()
    project = env.node_project
    if (
        global_install
        and manager is PackageManager.YARN
        and project is not None
        and project.yarn_berry
    ):
        local = add_dependency_command(
            manager=manager,
            spec=spec,
            global_install=False,
        )
        verb = "Upgrade" if upgrading else "Install"
        return (
            f"{verb} {package} manually: this project uses Yarn Berry (2 or "
            f"later), which has no global install, so lintro will not run "
            f"`yarn global add`. Run `{local}` to add it to the project, or "
            f"install it globally with another package manager."
        )
    return add_dependency_command(
        manager=manager,
        spec=spec,
        global_install=global_install,
    )


def _pin_conflict(
    env: InstallEnvironment,
    package: str,
    version: str,
    *,
    minimum: str,
    upgrading: bool,
) -> str | None:
    """Protect the spec a project declares from being rewritten by lintro.

    Only the *declared* spec is consulted, because that is what the project
    author wrote down and what an install or upgrade command would overwrite.
    The spec is classified by shape (#2045):

    - **Exact-compatible** (``3.9.4``, ``^3.9.4``, ``~3.9.4``, ``=3.9.4``,
      ``v3.9.4``, ``^v3.9.4``, ``3.9.4+build``; one operator and one ``v``
      stripped, build metadata dropped) is compared with lintro's version.
      A different version is a conflict that needs an explicit decision.
    - **Range** (``>=3.9.4``, ``3.9.x``, ``*``, ``||`` unions) that can
      resolve to *minimum* or later is not a conflict: the project's range
      governs. The install path installs the project's dependencies and lets
      the resolver satisfy it. The upgrade path does not rewrite the range
      either; it asks for a decision. A range whose upper bound is below
      *minimum* (``2.x``, ``<3``), or one whose bounds cannot be read, is a
      conflict and nothing runs: a project install would only end with a
      version lintro rejects.
    - **Opaque** (``workspace:``, ``npm:`` aliases, git and tarball URLs,
      local paths, dist-tags) is never called a conflict. lintro names the
      form and does not override it.

    A declared-but-missing package does not need a versioned add at all. It
    needs the project's own dependencies installed, which restores the
    declared version from the lockfile. ``npm install -D prettier@3.9.4``
    would rewrite ``^3.9.4`` (and turns ``>=3.9.4`` into ``^3.9.4``), so on
    the install path a matching or range declaration returns the manager's
    install-all command. The install plan announces that this installs the
    whole project's dependencies (:func:`whole_project_install_root`). On the
    upgrade path a matching exact declaration is not a conflict and the
    versioned add proceeds.

    Args:
        env: The current install environment.
        package: npm package name.
        version: Version lintro recommends.
        minimum: Lowest version lintro accepts.
        upgrading: Whether the caller is planning an upgrade of an already
            installed tool rather than a first install.

    Returns:
        A manual-action message, the manager's install-all command, or None
        when there is no declaration to protect (or an upgrade of a matching
        exact declaration should proceed).
    """
    project = env.node_project
    if project is None or env.prefer_global:
        return None
    declared = project.declared_spec(package)
    if declared is None:
        return None
    manager, _source = env.node_manager()
    restore = NODE_MANAGER_COMMANDS[manager].install_all
    adopt = add_dependency_command(
        manager=manager,
        spec=f"{package}@{version}",
        global_install=False,
    )
    kind = classify_declared_spec(declared)
    if kind is DeclaredSpecKind.OPAQUE:
        verb = "Upgrade" if upgrading else "Install"
        return (
            f"{verb} {package} through the project: this project manages "
            f"{package} via {describe_opaque_spec(declared)} (`{declared}`) "
            f"in package.json; lintro will not override it."
        )
    if kind is DeclaredSpecKind.RANGE:
        admits = range_admits_at_least(declared, minimum)
        if admits is not True:
            return _range_conflict(
                package,
                declared,
                excluded=admits is False,
                version=version,
                minimum=minimum,
                adopt=adopt,
                upgrading=upgrading,
            )
        if not upgrading:
            return restore
        return (
            f"Upgrade {package} explicitly: this project declares the range "
            f"`{declared}` in package.json, which lintro will not rewrite. "
            f"Update {package} within that range with {manager}, or run "
            f"`{adopt}` to pin lintro's version {version}."
        )
    if exact_version(declared) == version:
        return None if upgrading else restore
    difference = (
        f"this project declares {declared} in package.json but lintro "
        f"recommends {version}."
    )
    if upgrading:
        return (
            f"Upgrade {package} explicitly: {difference} "
            f"Run `{adopt}` to adopt lintro's version, or keep the project pin."
        )
    return (
        f"Install {package} from the project: {difference} "
        f"Run `{restore}` to install the version this project pins, or "
        f"`{adopt}` to adopt lintro's instead."
    )


def _range_conflict(
    package: str,
    declared: str,
    *,
    excluded: bool,
    version: str,
    minimum: str,
    adopt: str,
    upgrading: bool,
) -> str:
    """Explain a declared range that cannot be shown to reach lintro's minimum.

    Args:
        package: npm package name.
        declared: The declared range.
        excluded: True when the range's upper bound is below *minimum*; False
            when its bounds could not be read.
        version: Version lintro recommends.
        minimum: Lowest version lintro accepts.
        adopt: Command that pins lintro's version in the project.
        upgrading: Whether the caller is planning an upgrade.

    Returns:
        A manual-action message; nothing is run for it.
    """
    verb = "Upgrade" if upgrading else "Install"
    floor = (
        f"lintro's version {version}"
        if minimum == version
        else f"lintro's minimum {minimum} (lintro recommends {version})"
    )
    reach = (
        "does not admit"
        if excluded
        else "may not admit (lintro cannot read its bounds)"
    )
    return (
        f"{verb} {package} explicitly: this project declares the range "
        f"`{declared}` in package.json, which {reach} {floor}. Run `{adopt}` "
        f"to adopt lintro's version, or widen the range."
    )


def whole_project_install_root(
    env: InstallEnvironment,
    command: str,
) -> Path | None:
    """Return the project root when *command* installs the whole project.

    ``npm install`` (and its bun, pnpm and yarn equivalents) installs every
    dependency the manifest declares, runs install scripts and can update the
    lockfile. :func:`_pin_conflict` plans it for one declared tool, so the
    install plan has to say that it does more than install that tool (#2045).

    Args:
        env: The current install environment.
        command: A planned install command.

    Returns:
        The project root the command runs in, or None when the command is not
        a project-wide install.
    """
    project = env.node_project
    if project is None or env.installs_globally():
        return None
    project_wide = {commands.install_all for commands in NODE_MANAGER_COMMANDS.values()}
    return project.root if command in project_wide else None


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
