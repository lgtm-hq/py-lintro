"""Fail-closed npx fallback: trust the pin only when npx cannot reuse anything.

Node tools resolve a concrete local binary first, then a ``PATH`` binary,
then ``bunx <pkg>@<pin>``, then ``npx``. The npx command carries flags that
neutralise npm's workspace, bin-link and global settings. The pin is then
trusted only when no ``node_modules`` exists in the execution directory or
above it, no ``package.json`` there declares workspaces or is the package
itself, and no npm config selects a workspace; otherwise the tool is
unavailable (#2043). Real-runner coverage is in
``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.tools.core import snapshots
from lintro.tools.core.node_fallback import NPX_ISOLATION_FLAGS, npx_pin_refusal
from lintro.tools.prettier.definition import PrettierPlugin

_PIN = get_tool_version("prettier") or ""


def _has_npm_files_above(directory: Path) -> bool:
    """Report whether npm project files exist in *directory* or an ancestor.

    Args:
        directory: Directory to start from.

    Returns:
        True when a clean-project precondition cannot hold on this machine.
    """
    origin = directory.resolve()
    return any(
        (d / "node_modules").exists()
        or (d / "package.json").exists()
        or (d / ".npmrc").exists()
        for d in (origin, *origin.parents)
    )


@pytest.fixture
def clean_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A plain project with no npm state anywhere above it, and a private HOME.

    ``npx`` and ``node`` are hidden from ``shutil.which`` so the machine's
    builtin and global npmrc files are never consulted.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment and lookup overrides.

    Returns:
        The project directory.
    """
    if _has_npm_files_above(tmp_path):
        pytest.skip("npm project files exist above the temporary directory")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "package.json").write_text('{"name":"proj"}\n', encoding="utf-8")
    return project


def _refusal(cwd: Path, environ: dict[str, str] | None = None) -> str | None:
    """Ask the rule about *cwd* with an explicit environment.

    Args:
        cwd: Directory npx would run in.
        environ: Environment, empty by default.

    Returns:
        The refusal reason, or None.
    """
    return npx_pin_refusal(
        cwd=cwd,
        package="prettier",
        pin=_PIN,
        environ=environ or {},
    )


def _only_runner(monkeypatch: pytest.MonkeyPatch, runner: str, tmp_path: Path) -> None:
    """Leave *runner* as the only Node command on ``PATH``.

    Args:
        monkeypatch: Scoped override of ``shutil.which``.
        runner: ``"npx"`` or ``"bunx"``.
        tmp_path: Directory for the stand-in runner executable.
    """
    stand_in = tmp_path / runner
    stand_in.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    stand_in.chmod(0o755)

    def which(command: str, *args: object, **kwargs: object) -> str | None:
        return str(stand_in) if command == runner else None

    monkeypatch.setattr(shutil, "which", which)


# ---------------------------------------------------------------------------
# Project state
# ---------------------------------------------------------------------------


def test_clean_project_trusts_the_pin(clean_dir: Path) -> None:
    """Nothing to reuse and no workspace selection: npx can only run the pin.

    Args:
        clean_dir: Project without npm state above it.
    """
    assert_that(_refusal(clean_dir)).is_none()


@pytest.mark.parametrize("where", ["cwd", "parent", "grandparent"])
def test_node_modules_in_cwd_or_any_ancestor_refuses(
    clean_dir: Path,
    where: str,
) -> None:
    """Any reachable ``node_modules`` makes the pin untrustworthy.

    Args:
        clean_dir: Project without npm state above it.
        where: Which directory gets the ``node_modules``.
    """
    nested = clean_dir / "a" / "b"
    nested.mkdir(parents=True)
    owner = {"cwd": nested, "parent": nested.parent, "grandparent": clean_dir}[where]
    (owner / "node_modules").mkdir()

    reason = _refusal(nested)

    assert_that(reason).contains("only npx is available and a node_modules tree")
    assert_that(reason).contains((owner.resolve() / "node_modules").as_posix())
    assert_that(reason).contains(f"Install prettier@{_PIN} locally or install bun.")


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ({"name": "ws", "workspaces": ["packages/*"]}, "declares npm workspaces"),
        ({"name": "ws", "workspaces": {"packages": ["a"]}}, "declares npm workspaces"),
        ({"name": "prettier", "version": _PIN}, "is the prettier package itself"),
    ],
    ids=["workspaces-list", "workspaces-object", "package-itself"],
)
def test_package_json_that_npm_would_load_from_refuses(
    clean_dir: Path,
    manifest: dict[str, object],
    expected: str,
) -> None:
    """A workspace root or the package itself gives npm a tree to reuse.

    Args:
        clean_dir: Project without npm state above it.
        manifest: ``package.json`` written one level above the run directory.
        expected: Text the refusal must contain.
    """
    member = clean_dir / "packages" / "app"
    member.mkdir(parents=True)
    (clean_dir / "package.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert_that(_refusal(member)).contains(expected)


@pytest.mark.parametrize(
    "content",
    ["{not json", "[1, 2]"],
    ids=["invalid", "not-object"],
)
def test_unreadable_package_json_refuses(clean_dir: Path, content: str) -> None:
    """A manifest that cannot be read fails closed.

    Args:
        clean_dir: Project without npm state above it.
        content: Broken ``package.json`` content.
    """
    (clean_dir / "package.json").write_text(content, encoding="utf-8")

    assert_that(_refusal(clean_dir)).contains("package.json cannot be read")


# ---------------------------------------------------------------------------
# npm configuration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["npm_config_workspace", "NPM_CONFIG_WORKSPACE", "Npm_Config_Workspace"],
)
def test_workspace_selected_in_the_environment_refuses(
    clean_dir: Path,
    key: str,
) -> None:
    """``workspace=<name>`` cannot be cleared by a flag, in any key spelling.

    Args:
        clean_dir: Project without npm state above it.
        key: Environment variable spelling.
    """
    reason = _refusal(clean_dir, {key: "app"})

    assert_that(reason).contains(f"npm workspace selection is configured ({key}=app)")


def _project_rc(_tmp: Path, cwd: Path, env: dict[str, str]) -> Path:
    del env
    return cwd / ".npmrc"


def _ancestor_rc(tmp: Path, _cwd: Path, env: dict[str, str]) -> Path:
    del env
    return tmp / ".npmrc"


def _user_rc(tmp: Path, _cwd: Path, env: dict[str, str]) -> Path:
    del env
    return tmp / "home" / ".npmrc"


def _userconfig_env(tmp: Path, _cwd: Path, env: dict[str, str]) -> Path:
    env["NPM_CONFIG_USERCONFIG"] = str(tmp / "userrc")
    return tmp / "userrc"


def _userconfig_from_project(tmp: Path, cwd: Path, env: dict[str, str]) -> Path:
    del env
    (cwd / ".npmrc").write_text(
        f"userconfig={tmp / 'chained-userrc'}\n",
        encoding="utf-8",
    )
    return tmp / "chained-userrc"


def _globalconfig_env(tmp: Path, _cwd: Path, env: dict[str, str]) -> Path:
    env["npm_config_globalconfig"] = str(tmp / "globalrc")
    return tmp / "globalrc"


def _global_prefix_env(tmp: Path, _cwd: Path, env: dict[str, str]) -> Path:
    env["npm_config_prefix"] = str(tmp / "prefix")
    return tmp / "prefix" / "etc" / "npmrc"


_RC_SOURCES: dict[str, Callable[[Path, Path, dict[str, str]], Path]] = {
    "project": _project_rc,
    "ancestor": _ancestor_rc,
    "user-home": _user_rc,
    "userconfig-env": _userconfig_env,
    "userconfig-from-project": _userconfig_from_project,
    "globalconfig-env": _globalconfig_env,
    "global-prefix-env": _global_prefix_env,
}


@pytest.mark.parametrize("source", list(_RC_SOURCES), ids=list(_RC_SOURCES))
@pytest.mark.parametrize(
    "line",
    ["workspace=app", "workspace[]=app", '"workspace" = "app"', "WORKSPACE=app"],
    ids=["plain", "array", "quoted", "upper"],
)
def test_workspace_selected_in_any_npmrc_npm_reads_refuses(
    clean_dir: Path,
    tmp_path: Path,
    source: str,
    line: str,
) -> None:
    """Every npmrc location npm reads is checked for ``workspace``.

    Args:
        clean_dir: Project without npm state above it.
        tmp_path: Temporary directory provided by pytest.
        source: Which npmrc location holds the setting.
        line: How the setting is written.
    """
    env: dict[str, str] = {}
    rc = _RC_SOURCES[source](tmp_path, clean_dir, env)
    rc.parent.mkdir(parents=True, exist_ok=True)
    rc.write_text(f"; comment\n{line}\n", encoding="utf-8")

    reason = _refusal(clean_dir, env)

    assert_that(reason).contains(
        f"npm workspace selection is configured in {rc.resolve().as_posix()}",
    )


def test_builtin_and_node_prefix_npmrc_are_checked(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Npm's own ``npmrc`` and ``<node prefix>/etc/npmrc`` are read too.

    Args:
        clean_dir: Project without npm state above it.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped override of ``shutil.which``.
    """
    npm = tmp_path / "lib" / "node_modules" / "npm"
    (npm / "bin").mkdir(parents=True)
    (npm / "bin" / "npx-cli.js").write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "npx").symlink_to(npm / "bin" / "npx-cli.js")
    (bin_dir / "node").write_text("", encoding="utf-8")
    lookups = {"npx": str(bin_dir / "npx"), "node": str(bin_dir / "node")}
    monkeypatch.setattr(shutil, "which", lambda name, *_a, **_k: lookups.get(name))

    (npm / "npmrc").write_text("workspace=app\n", encoding="utf-8")
    builtin = _refusal(clean_dir)
    (npm / "npmrc").write_text("prefix=/nowhere\n", encoding="utf-8")
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "npmrc").write_text("workspace=app\n", encoding="utf-8")
    node_prefix = _refusal(clean_dir)

    assert_that(builtin).contains(
        f"configured in {(npm / 'npmrc').resolve().as_posix()}",
    )
    assert_that(node_prefix).contains("etc/npmrc")


@pytest.mark.parametrize(
    ("content", "trusted"),
    [
        ("; workspace=app\n# workspace=app\n", True),
        ("workspaces=true\nbin-links=false\nglobal=true\n", True),
        ("workspace=app\n", False),
    ],
    ids=["commented-out", "neutralised-settings", "selected"],
)
def test_only_workspace_selection_in_npmrc_refuses(
    clean_dir: Path,
    content: str,
    trusted: bool,
) -> None:
    """Settings the npx flags override do not refuse; comments are ignored.

    Args:
        clean_dir: Project without npm state above it.
        content: Project ``.npmrc`` content.
        trusted: Whether the pin must be trusted.
    """
    (clean_dir / ".npmrc").write_text(content, encoding="utf-8")

    reason = _refusal(clean_dir)

    if trusted:
        assert_that(reason).is_none()
    else:
        assert_that(reason).contains("npm workspace selection is configured")


def test_unreadable_npmrc_refuses(clean_dir: Path) -> None:
    """An npmrc that cannot be decoded fails closed.

    Args:
        clean_dir: Project without npm state above it.
    """
    (clean_dir / ".npmrc").write_bytes(b"workspace=\xff\xfe\n")

    assert_that(_refusal(clean_dir)).contains(".npmrc cannot be read")


@pytest.mark.parametrize(
    "environ",
    [
        {"NPM_CONFIG_BIN_LINKS": "false"},
        {"npm_config_bin_links": "0"},
        {"npm_config_workspaces": "true"},
        {"npm_config_global": "true"},
        {"npm_config_include_workspace_root": "true"},
    ],
    ids=["bin-links-false", "bin-links-zero", "workspaces", "global", "include-root"],
)
def test_settings_the_flags_override_are_trusted(
    clean_dir: Path,
    environ: dict[str, str],
) -> None:
    """The generated npx flags override these, so they do not refuse.

    Args:
        clean_dir: Project without npm state above it.
        environ: Setting the npx flags neutralise.
    """
    assert_that(_refusal(clean_dir, environ)).is_none()


# ---------------------------------------------------------------------------
# The gate, end to end through probe_tool
# ---------------------------------------------------------------------------


def test_npx_only_in_a_clean_project_reports_the_pin(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trusted npx case keeps the isolated, pinned command as evidence.

    Args:
        clean_dir: Project without npm state above it.
        tmp_path: Directory for the stand-in runner.
        monkeypatch: Scoped PATH override.
    """
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=clean_dir)
    verdict = verify_tool_version(PrettierPlugin().definition, cwd=str(clean_dir))

    assert_that(snapshot.command[:-2]).is_equal_to(
        ("npx", "--yes", *NPX_ISOLATION_FLAGS, "--package"),
    )
    assert_that(snapshot.command[-2]).is_equal_to(f"prettier@{_PIN}")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    assert_that(verdict).is_none()


def test_npx_only_beside_a_node_modules_tree_is_unavailable(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hoisted copy npx might run makes the tool unavailable, not passing.

    Args:
        clean_dir: Project without npm state above it.
        tmp_path: Directory for the stand-in runner.
        monkeypatch: Scoped PATH override.
    """
    member = clean_dir / "packages" / "app"
    member.mkdir(parents=True)
    (member / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    hoisted = clean_dir / "node_modules" / "prettier"
    hoisted.mkdir(parents=True)
    (hoisted / "package.json").write_text(
        json.dumps({"name": "prettier", "version": "1.0.0"}),
        encoding="utf-8",
    )
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=member)
    verdict = verify_tool_version(PrettierPlugin().definition, cwd=str(member))

    assert_that(snapshot.available).is_false()
    assert_that(snapshot.version).is_none()
    assert_that(snapshot.probe_error).contains("npx may run an unpinned copy")
    assert verdict is not None
    assert_that(verdict.unavailable_reason).contains("Install prettier@")


def test_concrete_local_binary_wins_over_the_npx_rule(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project-local binary is probed and run, though ``node_modules`` exists.

    Args:
        clean_dir: Project without npm state above it.
        tmp_path: Directory for the stand-in runner.
        monkeypatch: Scoped PATH override.
    """
    binary = clean_dir / "node_modules" / ".bin" / "prettier"
    binary.parent.mkdir(parents=True)
    binary.write_text(f'#!/bin/sh\nprintf "{_PIN}\\n"\n', encoding="utf-8")
    binary.chmod(0o755)
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=clean_dir)

    assert_that(snapshot.command).is_equal_to((binary.resolve().as_posix(),))
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)


def test_bunx_keeps_its_pin_beside_a_node_modules_tree(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With bunx, the pin stands even where npx would be refused.

    Args:
        clean_dir: Project without npm state above it.
        tmp_path: Directory for the stand-in runner.
        monkeypatch: Scoped PATH override.
    """
    (clean_dir / "node_modules").mkdir()
    _only_runner(monkeypatch, "bunx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=clean_dir)

    assert_that(snapshot.command[0]).is_equal_to("bunx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
