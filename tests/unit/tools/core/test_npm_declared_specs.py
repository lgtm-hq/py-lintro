"""Tests for how the npm strategy treats a project's declared spec (#2045).

Covers the three spec classes (exact-compatible, range, opaque), the
announcement of a whole-project install in the install plan, Yarn Berry's
missing global install, and the boundary for manager evidence.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from rich.console import Console

from lintro.cli_utils.commands.install import _display_plan
from lintro.enums.declared_spec_kind import DeclaredSpecKind
from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_hints import is_manual_hint
from lintro.tools.core.install_strategies import InstallEnvironment, get_strategy
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.declared_spec import (
    classify_declared_spec,
    exact_version,
)
from lintro.tools.core.install_strategies.node_project import detect_node_project
from lintro.tools.core.tool_installer import ToolInstaller
from lintro.tools.core.tool_registry import ManifestRegistry

PM = PackageManager

_EXACT_SPECS: tuple[str, ...] = ("3.9.4", "^3.9.4", "~3.9.4", "=3.9.4", "v3.9.4")

_RANGE_SPECS: tuple[str, ...] = (
    ">=3.9.4",
    ">= 3.9.4",
    ">=3.0.0 <4.0.0",
    "3.9.x",
    "3.x",
    "3",
    "^3.9",
    "*",
    "",
    "^2.0.0 || ^3.0.0",
    "3.9.x || >=4",
    "3.0.0 - 3.9.4",
    "=v3.9.4",
)

#: Opaque specs and the form each message must name.
_OPAQUE_SPECS: tuple[tuple[str, str], ...] = (
    ("workspace:*", "the workspace protocol"),
    ("workspace:^3.9.4", "the workspace protocol"),
    ("npm:prettier@3.9.4", "an npm alias"),
    ("npm:@acme/prettier-fork@^3", "an npm alias"),
    ("git+https://github.com/prettier/prettier.git#3.9.4", "a git dependency"),
    ("git+ssh://git@github.com/prettier/prettier.git", "a git dependency"),
    ("github:prettier/prettier#3.9.4", "a git dependency"),
    ("prettier/prettier#3.9.4", "a git dependency"),
    ("https://registry.npmjs.org/prettier/-/prettier-3.9.4.tgz", "a tarball URL"),
    ("file:../prettier", "a local path"),
    ("link:../prettier", "a local path"),
    ("catalog:", "a pnpm catalog"),
    ("latest", "a dist-tag or other non-version spec"),
    ("^^3.9.4", "a dist-tag or other non-version spec"),
)


def _project(
    root: Path,
    *,
    declared: str | None = None,
    package_manager: str | None = None,
    files: tuple[str, ...] = ("package-lock.json",),
) -> Path:
    """Write a Node project that may declare prettier.

    Args:
        root: Directory to write the project into.
        declared: devDependencies spec for prettier, if any.
        package_manager: ``packageManager`` field value, if any.
        files: Empty marker files to create (lockfiles, ``.yarnrc.yml``).

    Returns:
        The project root.
    """
    manifest: dict[str, object] = {"name": "demo"}
    if declared is not None:
        manifest["devDependencies"] = {"prettier": declared}
    if package_manager is not None:
        manifest["packageManager"] = package_manager
    (root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in files:
        (root / name).write_text("", encoding="utf-8")
    return root


def _env(
    root: Path,
    *,
    explicit: PackageManager | None = None,
    prefer_global: bool = False,
) -> InstallEnvironment:
    """Build an install environment for a project directory.

    Args:
        root: Directory to detect the Node project from.
        explicit: Manager the user named explicitly.
        prefer_global: Whether the user asked for global installs.

    Returns:
        The environment.
    """
    return InstallEnvironment(
        install_context=InstallContext.PIP,
        available_managers=frozenset({PM.NPM, PM.YARN}),
        node_project=detect_node_project(root),
        explicit_node_manager=explicit,
        prefer_global=prefer_global,
    )


def _npm() -> InstallStrategy:
    """Return the registered npm strategy.

    Returns:
        The strategy.
    """
    strategy = get_strategy("npm")
    assert strategy is not None  # narrow type for mypy
    return strategy


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _EXACT_SPECS)
def test_exact_compatible_specs_name_the_bare_version(spec: str) -> None:
    """One ``^ ~ = v`` prefix is stripped and the version compared.

    Args:
        spec: Declared spec under test.
    """
    assert_that(classify_declared_spec(spec)).is_equal_to(DeclaredSpecKind.EXACT)
    assert_that(exact_version(spec)).is_equal_to("3.9.4")


@pytest.mark.parametrize("spec", ["^^3.9.4", "vv3.9.4", "~^3.9.4", "=v3.9.4"])
def test_only_one_prefix_is_stripped(spec: str) -> None:
    """``lstrip`` with a character set used to strip any run of prefixes.

    Args:
        spec: Declared spec with more than one prefix character.
    """
    assert_that(exact_version(spec)).is_none()


@pytest.mark.parametrize("spec", _RANGE_SPECS)
def test_range_specs_are_classified_as_ranges(spec: str) -> None:
    """Comparators, wildcards, partial versions, hyphens and unions are ranges.

    Args:
        spec: Declared spec under test.
    """
    assert_that(classify_declared_spec(spec)).is_equal_to(DeclaredSpecKind.RANGE)


@pytest.mark.parametrize(("spec", "_form"), _OPAQUE_SPECS)
def test_non_version_specs_are_opaque(spec: str, _form: str) -> None:
    """Protocols, URLs, paths and tags are neither versions nor ranges.

    Args:
        spec: Declared spec under test.
        _form: Expected form label, unused here.
    """
    assert_that(classify_declared_spec(spec)).is_equal_to(DeclaredSpecKind.OPAQUE)


# ---------------------------------------------------------------------------
# Strategy hints per class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _RANGE_SPECS)
def test_declared_range_installs_through_the_project(
    spec: str,
    tmp_path: Path,
) -> None:
    """A declared range is not a conflict: the project install satisfies it.

    Args:
        spec: Declared range under test.
        tmp_path: Temporary directory provided by pytest.
    """
    env = _env(_project(tmp_path, declared=spec))

    hint = _npm().install_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(hint).is_equal_to("npm install")


@pytest.mark.parametrize("spec", [">=3.0.0", "3.x", "^2.0.0 || ^3.0.0"])
def test_upgrade_never_rewrites_a_declared_range(spec: str, tmp_path: Path) -> None:
    """A versioned add would turn ``>=3.0.0`` into ``^3.9.4``, so it is not run.

    The message names the range rather than calling it a conflicting pin.

    Args:
        spec: Declared range under test.
        tmp_path: Temporary directory provided by pytest.
    """
    env = _env(_project(tmp_path, declared=spec))

    hint = _npm().upgrade_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(is_manual_hint(hint)).is_true()
    assert_that(hint).contains(f"the range `{spec}`", "npm install -D prettier@3.9.4")
    assert_that(hint).does_not_contain("but lintro recommends")


@pytest.mark.parametrize(("spec", "form"), _OPAQUE_SPECS)
@pytest.mark.parametrize("upgrading", [False, True], ids=["install", "upgrade"])
def test_opaque_spec_gets_a_hint_naming_its_form(
    spec: str,
    form: str,
    upgrading: bool,
    tmp_path: Path,
) -> None:
    """``workspace:*`` and friends are named honestly, never called a conflict.

    Args:
        spec: Declared opaque spec under test.
        form: Form the message must name.
        upgrading: Whether the upgrade path is planned.
        tmp_path: Temporary directory provided by pytest.
    """
    env = _env(_project(tmp_path, declared=spec))
    hint_for = _npm().upgrade_hint if upgrading else _npm().install_hint

    hint = hint_for(env, "prettier", "3.9.4", "prettier", None)

    assert_that(is_manual_hint(hint)).is_true()
    assert_that(hint).contains(
        f"manages prettier via {form} (`{spec}`)",
        "lintro will not override it",
    )
    assert_that(hint).does_not_contain("recommends", "npm install -D")


def test_differing_exact_pin_is_still_a_conflict(tmp_path: Path) -> None:
    """The exact-compatible class keeps the existing conflict report.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    env = _env(_project(tmp_path, declared="^3.1.0"))

    hint = _npm().install_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(hint).starts_with("Install prettier from the project")
    assert_that(hint).contains("declares ^3.1.0", "recommends 3.9.4")


# ---------------------------------------------------------------------------
# Whole-project install is announced
# ---------------------------------------------------------------------------


def _plan_output(root: Path) -> tuple[dict[str, Path], str]:
    """Plan a prettier install from *root* and render the plan.

    Args:
        root: Project directory to plan from.

    Returns:
        The plan's project installs and the rendered plan text.
    """
    installer = ToolInstaller(
        ManifestRegistry.load(),
        RuntimeContext(
            install_context=InstallContext.PIP,
            platform_label="test",
            environment=_env(root),
            is_ci=False,
        ),
    )
    with patch.object(ToolInstaller, "_get_installed_version", return_value=None):
        plan = installer.plan(tools=["prettier"])
    buffer = io.StringIO()
    _display_plan(Console(file=buffer, width=400, color_system=None), plan)
    return plan.project_installs, buffer.getvalue()


@pytest.mark.parametrize("spec", ["^3.9.4", ">=3.0.0"], ids=["exact", "range"])
def test_plan_announces_a_whole_project_install(spec: str, tmp_path: Path) -> None:
    """``npm install`` for one tool installs everything; the plan says so.

    Args:
        spec: Declared spec that leads to the project install.
        tmp_path: Temporary directory provided by pytest.
    """
    root = _project(tmp_path, declared=spec).resolve()

    project_installs, output = _plan_output(root)

    assert_that(project_installs).is_equal_to({"prettier": root})
    assert_that(output).contains(
        "installs the whole project's dependencies "
        f"(`npm install` in {root}), not only prettier",
    )


def test_plan_does_not_announce_a_single_package_add(tmp_path: Path) -> None:
    """An undeclared tool gets a versioned add, which needs no announcement.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    root = _project(tmp_path).resolve()

    project_installs, output = _plan_output(root)

    assert_that(project_installs).is_empty()
    assert_that(output).contains("npm install -D prettier@")
    assert_that(output).does_not_contain("whole project")


# ---------------------------------------------------------------------------
# Yarn Berry has no global install
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("package_manager", "files"),
    [
        ("yarn@4.5.0", ("yarn.lock",)),
        ("yarn@2.4.3+sha512.abc", ("yarn.lock",)),
        (None, ("yarn.lock", ".yarnrc.yml")),
        ("yarn", ("yarn.lock", ".yarnrc.yml")),
    ],
    ids=["field-4", "field-2-hash", "yarnrc", "unversioned-field-yarnrc"],
)
@pytest.mark.parametrize("upgrading", [False, True], ids=["install", "upgrade"])
def test_berry_refuses_the_global_path(
    package_manager: str | None,
    files: tuple[str, ...],
    upgrading: bool,
    tmp_path: Path,
) -> None:
    """Yarn 2+ removed ``yarn global``, so no command is planned for it.

    Args:
        package_manager: ``packageManager`` field, if any.
        files: Marker files in the project.
        upgrading: Whether the upgrade path is planned.
        tmp_path: Temporary directory provided by pytest.
    """
    root = _project(tmp_path, package_manager=package_manager, files=files)
    env = _env(root, explicit=PM.YARN, prefer_global=True)
    hint_for = _npm().upgrade_hint if upgrading else _npm().install_hint

    hint = hint_for(env, "prettier", "3.9.4", "prettier", None)

    assert_that(is_manual_hint(hint)).is_true()
    assert_that(hint).contains("Yarn Berry", "yarn add --dev prettier@3.9.4")
    assert_that(hint).does_not_contain("yarn global add prettier")


@pytest.mark.parametrize(
    ("package_manager", "files"),
    [
        ("yarn@1.22.22", ("yarn.lock",)),
        ("yarn@1.22.22", ("yarn.lock", ".yarnrc.yml")),
        (None, ("yarn.lock",)),
    ],
    ids=["field-1", "field-1-beats-yarnrc", "lockfile-only"],
)
def test_yarn_classic_keeps_yarn_global_add(
    package_manager: str | None,
    files: tuple[str, ...],
    tmp_path: Path,
) -> None:
    """Yarn 1 still supports ``yarn global add``.

    Args:
        package_manager: ``packageManager`` field, if any.
        files: Marker files in the project.
        tmp_path: Temporary directory provided by pytest.
    """
    root = _project(tmp_path, package_manager=package_manager, files=files)
    env = _env(root, explicit=PM.YARN, prefer_global=True)

    hint = _npm().install_hint(env, "prettier", "3.9.4", "prettier", None)

    assert_that(hint).is_equal_to("yarn global add prettier@3.9.4")


def test_berry_project_local_add_is_unchanged(tmp_path: Path) -> None:
    """Only the global path changes; a Berry dev dependency still installs.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    root = _project(tmp_path, package_manager="yarn@4.5.0", files=("yarn.lock",))

    hint = _npm().install_hint(_env(root), "prettier", "3.9.4", "prettier", None)

    assert_that(hint).is_equal_to("yarn add --dev prettier@3.9.4")


# ---------------------------------------------------------------------------
# Manager evidence stops at the search boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ancestor_manifest", "ancestor_files"),
    [
        ({"packageManager": "pnpm@9.1.0"}, ()),
        ({"packageManager": "yarn@4.5.0"}, (".yarnrc.yml",)),
    ],
    ids=["package-manager-field", "berry-markers"],
)
def test_ancestor_outside_a_repository_does_not_supply_evidence(
    ancestor_manifest: dict[str, str],
    ancestor_files: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no ``.git`` and outside ``$HOME``, the nearest manifest is the bound.

    Args:
        ancestor_manifest: Manifest written into the unrelated ancestor.
        ancestor_files: Marker files written beside it.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    (tmp_path / "package.json").write_text(
        json.dumps(ancestor_manifest),
        encoding="utf-8",
    )
    for name in ancestor_files:
        (tmp_path / name).write_text("", encoding="utf-8")
    project = tmp_path / "downloaded"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")

    detected = detect_node_project(project)

    assert detected is not None  # narrow type for mypy
    assert_that(detected.declared_manager).is_none()
    assert_that(detected.yarn_berry).is_false()


def test_workspace_member_inherits_berry_from_the_repository_root(
    tmp_path: Path,
) -> None:
    """Inside a repository the root's ``.yarnrc.yml`` covers a member package.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".git").mkdir()
    _project(tmp_path, files=("yarn.lock", ".yarnrc.yml"))
    member = tmp_path / "packages" / "web"
    member.mkdir(parents=True)
    (member / "package.json").write_text("{}", encoding="utf-8")

    detected = detect_node_project(member)

    assert detected is not None  # narrow type for mypy
    assert_that(detected.root).is_equal_to(member.resolve())
    assert_that(detected.yarn_berry).is_true()
