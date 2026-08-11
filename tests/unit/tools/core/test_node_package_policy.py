"""Tests for the Node.js package-manager selection and install-scope policy.

Covers issue #2005: which manager ``lintro install`` picks, whether it installs
project-locally or globally, and its refusal to silently replace a project pin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.enums.node_manager_source import NodeManagerSource
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_strategies import InstallEnvironment, get_strategy
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.node_project import (
    NODE_MANAGER_COMMANDS,
    NODE_MANAGERS,
    add_dependency_command,
    detect_node_project,
    select_node_manager,
)
from lintro.tools.core.tool_installer import ToolInstaller
from lintro.tools.core.tool_registry import ManifestRegistry

PM = PackageManager


def _write_project(
    root: Path,
    *,
    lockfile: str | None = None,
    package_manager: str | None = None,
    dev_dependencies: dict[str, str] | None = None,
) -> Path:
    """Create a minimal Node project on disk.

    Args:
        root: Directory to write the project into.
        lockfile: Lockfile name to create, if any.
        package_manager: Value for the ``packageManager`` field, if any.
        dev_dependencies: devDependencies table, if any.

    Returns:
        The project root.
    """
    manifest: dict[str, object] = {"name": "demo"}
    if package_manager is not None:
        manifest["packageManager"] = package_manager
    if dev_dependencies is not None:
        manifest["devDependencies"] = dev_dependencies
    (root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    if lockfile is not None:
        (root / lockfile).write_text("", encoding="utf-8")
    return root


def _npm_strategy() -> InstallStrategy:
    """Return the registered npm install strategy.

    Returns:
        The npm strategy instance.
    """
    strategy = get_strategy("npm")
    assert strategy is not None  # narrow type for mypy
    return strategy


def _env(
    root: Path | None,
    *,
    managers: frozenset[PackageManager] = frozenset({PM.BUN, PM.NPM}),
    explicit: PackageManager | None = None,
    prefer_global: bool = False,
) -> InstallEnvironment:
    """Build an InstallEnvironment anchored on a directory.

    Args:
        root: Directory to detect a Node project from, or None for no project.
        managers: Package managers to report as available.
        explicit: Manager the user named explicitly.
        prefer_global: Whether the user asked for global installs.

    Returns:
        An InstallEnvironment instance.
    """
    return InstallEnvironment(
        install_context=InstallContext.PIP,
        available_managers=managers,
        node_project=detect_node_project(root) if root is not None else None,
        explicit_node_manager=explicit,
        prefer_global=prefer_global,
    )


def _installer(
    registry: ManifestRegistry,
    root: Path | None,
    *,
    prefer_global: bool = False,
) -> ToolInstaller:
    """Build a ToolInstaller anchored on a directory.

    Args:
        registry: Manifest registry to plan against.
        root: Directory to detect a Node project from, or None for no project.
        prefer_global: Whether the user asked for global installs.

    Returns:
        A ToolInstaller instance.
    """
    return ToolInstaller(
        registry,
        RuntimeContext(
            install_context=InstallContext.PIP,
            platform_label="test",
            environment=_env(root, prefer_global=prefer_global),
            is_ci=False,
        ),
    )


# ---------------------------------------------------------------------------
# Project detection
# ---------------------------------------------------------------------------


def test_detect_node_project_walks_up_from_a_subdirectory(tmp_path: Path) -> None:
    """A subdirectory resolves the enclosing project, matching runtime lookup.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    nested = tmp_path / "packages" / "web" / "src"
    nested.mkdir(parents=True)

    project = detect_node_project(nested)

    assert_that(project).is_not_none()
    assert project is not None  # narrow type for mypy
    assert_that(project.root).is_equal_to(tmp_path.resolve())


def test_detect_node_project_returns_none_without_a_manifest(tmp_path: Path) -> None:
    """No package.json anywhere above means no project.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    nested = tmp_path / "nope"
    nested.mkdir()

    assert_that(detect_node_project(nested)).is_none()


def test_detect_node_project_survives_malformed_manifest(tmp_path: Path) -> None:
    """A package.json that is not valid JSON still identifies the project root.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("", encoding="utf-8")

    project = detect_node_project(tmp_path)

    assert_that(project).is_not_none()
    assert project is not None  # narrow type for mypy
    assert_that(project.lockfile_manager).is_equal_to(PM.NPM)
    assert_that(project.dependencies).is_empty()


def test_declared_spec_reads_dev_dependencies(tmp_path: Path) -> None:
    """The declared spec for a package comes from the manifest.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, dev_dependencies={"prettier": "^3.1.0"})

    project = detect_node_project(tmp_path)

    assert project is not None  # narrow type for mypy
    assert_that(project.declared_spec("prettier")).is_equal_to("^3.1.0")
    assert_that(project.declared_spec("oxlint")).is_none()


# ---------------------------------------------------------------------------
# Manager selection policy
# ---------------------------------------------------------------------------


def test_explicit_choice_beats_project_metadata(tmp_path: Path) -> None:
    """An explicit manager overrides packageManager and lockfile evidence.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="bun.lock", package_manager="pnpm@9.1.0")

    manager, source = select_node_manager(
        available=frozenset({PM.BUN}),
        project=detect_node_project(tmp_path),
        explicit=PM.NPM,
    )

    assert_that(manager).is_equal_to(PM.NPM)
    assert_that(source).is_equal_to(NodeManagerSource.EXPLICIT)


def test_package_manager_field_beats_lockfile(tmp_path: Path) -> None:
    """The packageManager field is more authoritative than a stale lockfile.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json", package_manager="pnpm@9.1.0")

    manager, source = select_node_manager(
        available=frozenset({PM.BUN, PM.NPM}),
        project=detect_node_project(tmp_path),
    )

    assert_that(manager).is_equal_to(PM.PNPM)
    assert_that(source).is_equal_to(NodeManagerSource.PACKAGE_MANAGER_FIELD)


def test_lockfile_beats_installed_bun(tmp_path: Path) -> None:
    """An npm lockfile wins even when bun is on PATH — the original bug (#2005).

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")

    manager, source = select_node_manager(
        available=frozenset({PM.BUN, PM.NPM}),
        project=detect_node_project(tmp_path),
    )

    assert_that(manager).is_equal_to(PM.NPM)
    assert_that(source).is_equal_to(NodeManagerSource.LOCKFILE)


def test_available_manager_is_the_last_resort(tmp_path: Path) -> None:
    """With no project evidence, lintro's own preference decides.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path)

    manager, source = select_node_manager(
        available=frozenset({PM.BUN, PM.NPM}),
        project=detect_node_project(tmp_path),
    )

    assert_that(manager).is_equal_to(PM.BUN)
    assert_that(source).is_equal_to(NodeManagerSource.AVAILABLE_FALLBACK)


def test_unrecognised_package_manager_field_falls_through(tmp_path: Path) -> None:
    """An unknown packageManager value does not derail selection.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json", package_manager="corn@1.0.0")

    manager, source = select_node_manager(
        available=frozenset({PM.BUN, PM.NPM}),
        project=detect_node_project(tmp_path),
    )

    assert_that(manager).is_equal_to(PM.NPM)
    assert_that(source).is_equal_to(NodeManagerSource.LOCKFILE)


def test_selection_does_not_require_the_manager_to_be_installed(
    tmp_path: Path,
) -> None:
    """A project's manager is reported even when it is not on PATH.

    Telling the user to run npm in their npm-locked project is more useful than
    quietly writing a bun lockfile into it.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")

    manager, _source = select_node_manager(
        available=frozenset({PM.BUN}),
        project=detect_node_project(tmp_path),
    )

    assert_that(manager).is_equal_to(PM.NPM)


# ---------------------------------------------------------------------------
# Local vs global scope
# ---------------------------------------------------------------------------


def test_inside_a_project_installs_a_dev_dependency(tmp_path: Path) -> None:
    """A project manifest means the tool is added as a dev dependency.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    env = _env(tmp_path)

    hint = _npm_strategy().install_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(env.installs_globally()).is_false()
    assert_that(hint).is_equal_to("npm install -D prettier@3.9.4")


def test_without_a_project_installs_globally() -> None:
    """No manifest means there is nothing project-local to install into."""
    env = _env(None)

    hint = _npm_strategy().install_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(env.installs_globally()).is_true()
    assert_that(hint).is_equal_to("bun add -g prettier@3.9.4")


def test_explicit_global_flag_overrides_the_project(tmp_path: Path) -> None:
    """``--global`` is the documented escape hatch inside a project.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    env = _env(tmp_path, prefer_global=True)

    hint = _npm_strategy().install_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(hint).is_equal_to("npm install -g prettier@3.9.4")


def test_brew_does_not_pre_empt_a_project_dependency(tmp_path: Path) -> None:
    """A machine-wide formula must not win over the project's own dep set.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    env = _env(
        tmp_path,
        managers=frozenset({PM.BREW, PM.BUN, PM.NPM}),
    )

    hint = _npm_strategy().install_hint(
        env,
        "markdownlint",
        "0.23.2",
        "markdownlint-cli2",
        None,
    )

    assert_that(hint).is_equal_to("npm install -D markdownlint-cli2@0.23.2")


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        (PM.BUN, "bun add -D prettier@3.9.4"),
        (PM.NPM, "npm install -D prettier@3.9.4"),
        (PM.PNPM, "pnpm add -D prettier@3.9.4"),
        (PM.YARN, "yarn add --dev prettier@3.9.4"),
    ],
    ids=["manager=bun", "manager=npm", "manager=pnpm", "manager=yarn"],
)
def test_dev_dependency_command_per_manager(
    manager: PackageManager,
    expected: str,
) -> None:
    """Each supported manager gets its own correct add-a-dev-dependency syntax.

    Args:
        manager: Package manager under test.
        expected: Expected shell command.
    """
    command = add_dependency_command(
        manager=manager,
        spec="prettier@3.9.4",
        global_install=False,
    )

    assert_that(command).is_equal_to(expected)


def test_yarn_global_puts_the_scope_before_the_verb() -> None:
    """Yarn Classic spells a global add as ``yarn global add``."""
    command = add_dependency_command(
        manager=PM.YARN,
        spec="prettier@3.9.4",
        global_install=True,
    )

    assert_that(command).is_equal_to("yarn global add prettier@3.9.4")


# ---------------------------------------------------------------------------
# Project pins are never replaced implicitly
# ---------------------------------------------------------------------------


def test_upgrade_reports_a_conflicting_project_pin(tmp_path: Path) -> None:
    """A pin that differs from the recommendation requires an explicit decision.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(
        tmp_path,
        lockfile="package-lock.json",
        dev_dependencies={"prettier": "3.1.0"},
    )
    env = _env(tmp_path)

    hint = _npm_strategy().upgrade_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(hint).starts_with("Upgrade prettier explicitly")
    assert_that(hint).contains("3.1.0", "3.9.4", "npm install -D prettier@3.9.4")


def test_upgrade_proceeds_when_the_pin_already_matches(tmp_path: Path) -> None:
    """A caret range that admits the recommendation is not a conflict.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(
        tmp_path,
        lockfile="package-lock.json",
        dev_dependencies={"prettier": "^3.9.4"},
    )
    env = _env(tmp_path)

    hint = _npm_strategy().upgrade_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(hint).is_equal_to("npm install -D prettier@3.9.4")


def test_upgrade_of_an_undeclared_package_is_not_a_conflict(tmp_path: Path) -> None:
    """A package the project does not declare has no pin to protect.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(
        tmp_path,
        lockfile="package-lock.json",
        dev_dependencies={"prettier": "3.1.0"},
    )
    env = _env(tmp_path)

    hint = _npm_strategy().upgrade_hint(env, "oxlint", "1.75.0", "oxlint", None)

    assert_that(hint).is_equal_to("npm install -D oxlint@1.75.0")


def test_explicit_global_upgrade_ignores_the_project_pin(tmp_path: Path) -> None:
    """``--global`` targets a different install, so the project pin is untouched.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(
        tmp_path,
        lockfile="package-lock.json",
        dev_dependencies={"prettier": "3.1.0"},
    )
    env = _env(tmp_path, prefer_global=True)

    hint = _npm_strategy().upgrade_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(hint).is_equal_to("npm install -g prettier@3.9.4")


# ---------------------------------------------------------------------------
# Install planning agrees with runtime resolution
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="fake shell-script binary is not executable on Windows",
)
def test_planning_reports_the_project_local_version(tmp_path: Path) -> None:
    """Planning reports the version a run would use, not whatever is on PATH.

    Since #1811 a check runs the project-local ``node_modules/.bin`` install, so
    planning against ``PATH`` would report a different tool than the one that
    actually runs — the "two authorities" split #2005 is about. Asserted through
    the public-ish planning entry point rather than the resolver helper, so the
    test proves the reported *status*, not an internal call shape.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    local_bin = tmp_path / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    binary = local_bin / "prettier"
    binary.write_text('#!/bin/sh\necho "3.1.0"\n', encoding="utf-8")
    binary.chmod(0o755)

    registry = ManifestRegistry.load()
    installer = _installer(registry, tmp_path)

    version = installer._get_installed_version(registry.get("prettier"))

    assert_that(version).is_equal_to("3.1.0")


def test_planning_leaves_non_npm_tools_on_path() -> None:
    """A pip-installed tool keeps its manifest version command verbatim."""
    registry = ManifestRegistry.load()
    installer = _installer(registry, None)
    tool = registry.get("ruff")

    command = installer._resolved_version_command(tool)

    assert_that(command).is_equal_to(list(tool.version_command))


def test_local_install_runs_from_the_project_root(tmp_path: Path) -> None:
    """A project-local install command runs in the detected project root.

    Package managers walk up to the nearest ``package.json``, but a nested
    working directory must not add the dependency to a manifest lintro never
    looked at.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    registry = ManifestRegistry.load()

    local = _installer(registry, tmp_path)
    globally = _installer(registry, tmp_path, prefer_global=True)

    assert_that(local._install_cwd(registry.get("prettier"))).is_equal_to(
        tmp_path.resolve(),
    )
    assert_that(globally._install_cwd(registry.get("prettier"))).is_none()
    assert_that(local._install_cwd(registry.get("ruff"))).is_none()


def test_workspace_package_inherits_the_repository_lockfile(tmp_path: Path) -> None:
    """A nested manifest without its own lockfile uses the repo root's.

    Workspace packages rarely carry a lockfile or a packageManager field; both
    live at the repository root. Stopping the search at the nested manifest
    would downgrade the decision to "whichever manager is installed".

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    workspace = tmp_path / "packages" / "web"
    workspace.mkdir(parents=True)
    _write_project(workspace, dev_dependencies={"prettier": "3.1.0"})

    project = detect_node_project(workspace)

    assert project is not None  # narrow type for mypy
    assert_that(project.root).is_equal_to(workspace.resolve())
    assert_that(project.lockfile_manager).is_equal_to(PM.NPM)
    assert_that(project.declared_spec("prettier")).is_equal_to("3.1.0")


def test_workspace_package_manager_field_overrides_the_root(tmp_path: Path) -> None:
    """Nearest evidence wins: a workspace's own field beats the repo root's.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="bun.lock", package_manager="bun@1.2.0")
    workspace = tmp_path / "packages" / "web"
    workspace.mkdir(parents=True)
    _write_project(workspace, package_manager="pnpm@9.1.0")

    project = detect_node_project(workspace)

    assert project is not None  # narrow type for mypy
    assert_that(project.declared_manager).is_equal_to(PM.PNPM)


@pytest.mark.parametrize(
    "manager",
    [PM.PNPM, PM.YARN],
    ids=["manager=pnpm", "manager=yarn"],
)
def test_pnpm_or_yarn_alone_satisfies_prerequisites(
    manager: PackageManager,
) -> None:
    """A pnpm- or yarn-only machine can install Node tools.

    Args:
        manager: The single available Node package manager.
    """
    env = InstallEnvironment(
        install_context=InstallContext.PIP,
        available_managers=frozenset({manager}),
    )
    strategy = _npm_strategy()

    assert_that(strategy.is_available(env)).is_true()
    assert_that(strategy.check_prerequisites(env, "prettier")).is_none()


def test_search_stops_at_the_repository_boundary(tmp_path: Path) -> None:
    """A package.json above the repository root is never treated as the project.

    Without the boundary a stray ``~/package.json`` would flip lintro from a
    global install to writing a dev dependency into an unrelated manifest.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    nested = repo / "src"
    nested.mkdir()

    assert_that(detect_node_project(nested)).is_none()


def test_repository_root_manifest_is_still_found(tmp_path: Path) -> None:
    """The boundary is inclusive: a repo root is a legitimate project root.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".git").mkdir()
    _write_project(tmp_path, lockfile="package-lock.json")
    nested = tmp_path / "src"
    nested.mkdir()

    project = detect_node_project(nested)

    assert project is not None  # narrow type for mypy
    assert_that(project.root).is_equal_to(tmp_path.resolve())


def test_git_worktree_file_also_bounds_the_search(tmp_path: Path) -> None:
    """``.git`` is a file in a worktree or submodule, and still bounds the walk.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    assert_that(detect_node_project(repo)).is_none()


def test_brew_wins_when_no_node_manager_exists(tmp_path: Path) -> None:
    """Inside a project, brew still wins on a machine with no npm or bun.

    Prerequisites pass on brew alone, so planning ``npm install -D`` here would
    emit a command the machine cannot run.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    env = _env(tmp_path, managers=frozenset({PM.BREW}))
    strategy = _npm_strategy()

    assert_that(strategy.check_prerequisites(env, "markdownlint")).is_none()
    hint = strategy.install_hint(
        env,
        "markdownlint",
        "0.23.2",
        "markdownlint-cli2",
        None,
    )

    assert_that(hint).is_equal_to("brew install markdownlint-cli2")


def test_declared_but_missing_package_does_not_rewrite_the_pin(tmp_path: Path) -> None:
    """A declared-but-uninstalled package reaches planning as *missing*.

    Without the guard on the install path, lintro would emit a versioned
    ``-D`` add and rewrite the project's pin — the same failure as the upgrade
    path, reached from the other direction.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(
        tmp_path,
        lockfile="package-lock.json",
        dev_dependencies={"prettier": "3.1.0"},
    )
    env = _env(tmp_path)

    hint = _npm_strategy().install_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(hint).starts_with("Install prettier from the project")
    assert_that(hint).contains("3.1.0", "3.9.4", "npm install")
    assert_that(hint).does_not_contain("npm install -D prettier@3.9.4 ")


def test_undeclared_package_still_installs_normally(tmp_path: Path) -> None:
    """A package the project does not declare has no pin to protect.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(
        tmp_path,
        lockfile="package-lock.json",
        dev_dependencies={"prettier": "3.1.0"},
    )
    env = _env(tmp_path)

    hint = _npm_strategy().install_hint(env, "oxlint", "1.75.0", "oxlint", None)

    assert_that(hint).is_equal_to("npm install -D oxlint@1.75.0")


def test_install_hint_is_routed_to_the_manual_list(tmp_path: Path) -> None:
    """The conflict message must be recognised as manual, not run as a command.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(
        tmp_path,
        lockfile="package-lock.json",
        dev_dependencies={"prettier": "3.1.0"},
    )
    env = _env(tmp_path)
    hint = _npm_strategy().install_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(ToolInstaller._is_manual_hint(hint)).is_true()


def test_home_is_not_treated_as_the_project_root(tmp_path: Path) -> None:
    """A stray ``~/package.json`` never becomes the project of a nested cwd.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")
    nested = tmp_path / "scratch" / "notes"
    nested.mkdir(parents=True)

    with patch(
        "lintro.tools.core.install_strategies.node_project.Path.home",
        return_value=tmp_path,
    ):
        assert_that(detect_node_project(nested)).is_none()


def test_starting_in_home_still_finds_its_manifest(tmp_path: Path) -> None:
    """Running *in* ``$HOME`` is a deliberate cwd, so its manifest counts.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_project(tmp_path, lockfile="package-lock.json")

    with patch(
        "lintro.tools.core.install_strategies.node_project.Path.home",
        return_value=tmp_path,
    ):
        project = detect_node_project(tmp_path)

    assert project is not None  # narrow type for mypy
    assert_that(project.root).is_equal_to(tmp_path.resolve())


def test_every_selectable_manager_has_command_spellings() -> None:
    """Selection can only ever return a manager the command table knows.

    ``add_dependency_command`` indexes the table directly, so a manager that
    selection can return but the table lacks would be a ``KeyError`` at plan
    time. Deriving ``NODE_MANAGERS`` from the table makes that impossible;
    this asserts the invariant rather than trusting the derivation to survive.
    """
    for manager in NODE_MANAGERS:
        commands = NODE_MANAGER_COMMANDS[manager]
        for spelling in (commands.dev_add, commands.global_add, commands.install_all):
            assert_that(spelling).described_as(manager.value).starts_with(manager.value)

    # The fallback manager is hardcoded and must also be covered.
    assert_that(NODE_MANAGERS).contains(PM.NPM)
