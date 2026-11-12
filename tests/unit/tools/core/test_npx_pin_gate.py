"""Fail-closed npx fallback: trust the pin only when npx cannot reuse anything.

Node tools resolve a concrete local binary first, then a ``PATH`` binary,
then ``bunx <pkg>@<pin>``, then ``npx``. The npx command carries flags that
neutralise npm's workspace, bin-link, global and offline settings, plus a
lintro-owned ``--cache``. The pin is then trusted only when no
``node_modules`` exists in the execution directory or above it, no
``package.json`` there declares workspaces or is the package itself, and the
environment does not select a workspace; otherwise the tool is unavailable
(#2043). Settings in npmrc files are left to npm: the flags win or npm fails
loudly. Real-runner coverage is in
``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.tools.core import snapshots
from lintro.tools.core.node_fallback import (
    NPX_ISOLATION_FLAGS,
    npx_cache_dir,
    npx_pin_refusal,
    npx_runner_options,
    registry_fallback_spec,
)
from lintro.tools.prettier.definition import PrettierPlugin
from lintro.utils import file_cache

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
    """A plain project with no npm state above it, a private HOME and cache.

    ``npx`` and ``node`` are hidden from ``shutil.which`` so a real install on
    the machine cannot leak into these probes.

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
    monkeypatch.setattr(file_cache, "CACHE_DIR", tmp_path / "lintro-cache")
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


@pytest.mark.parametrize(
    "content",
    ["workspace=app\n", "\ufeffworkspace=app\n", "userconfig=bad\x00path\n"],
    ids=["workspace", "bom", "nul-byte"],
)
def test_npmrc_files_are_left_to_npm(clean_dir: Path, content: str) -> None:
    """Lintro never parses npmrc files; npm wins or fails loudly.

    A ``workspace`` in a file conflicts with ``--workspaces=false`` and npm
    exits with an error, which is reported as an execution failure.

    Args:
        clean_dir: Project without npm state above it.
        content: Project ``.npmrc`` content.
    """
    (clean_dir / ".npmrc").write_text(content, encoding="utf-8")

    assert_that(_refusal(clean_dir)).is_none()


# ---------------------------------------------------------------------------
# lintro's own npm cache
# ---------------------------------------------------------------------------


def test_runner_options_end_with_lintros_own_cache(clean_dir: Path) -> None:
    """Every npx fallback names lintro's cache after the fixed flags.

    Args:
        clean_dir: Project without npm state above it (sets the cache root).
    """
    del clean_dir
    options = npx_runner_options()

    assert_that(options[:-1]).is_equal_to(list(NPX_ISOLATION_FLAGS))
    assert_that(options[-1]).is_equal_to(f"--cache={npx_cache_dir()}")
    assert_that(NPX_ISOLATION_FLAGS).contains(
        "--offline=false",
        "--prefer-offline=false",
    )


def test_spec_is_recovered_past_the_cache_option(clean_dir: Path) -> None:
    """The cache option is skipped like the other runner flags.

    Args:
        clean_dir: Project without npm state above it (sets the cache root).
    """
    del clean_dir
    command = [
        "npx",
        "--yes",
        *npx_runner_options(),
        "--package",
        f"prettier@{_PIN}",
        "prettier",
    ]

    assert_that(registry_fallback_spec(command)).is_equal_to(f"prettier@{_PIN}")


def _npx_command(*options: str) -> list[str]:
    """Build an npx fallback command with the given runner options.

    Args:
        *options: Options between ``--yes`` and ``--package``.

    Returns:
        The command.
    """
    return ["npx", "--yes", *options, "--package", f"prettier@{_PIN}", "prettier"]


def test_command_with_lintros_cache_is_trusted(clean_dir: Path) -> None:
    """The generated command passes the cache check.

    Args:
        clean_dir: Project without npm state above it.
    """
    reason = npx_pin_refusal(
        cwd=clean_dir,
        package="prettier",
        pin=_PIN,
        environ={},
        command=_npx_command(*npx_runner_options()),
    )

    assert_that(reason).is_none()


@pytest.mark.parametrize(
    "options",
    [
        list(NPX_ISOLATION_FLAGS),
        [*NPX_ISOLATION_FLAGS, "--cache=/somewhere/else"],
    ],
    ids=["no-cache-option", "foreign-cache"],
)
def test_command_without_lintros_cache_is_refused(
    clean_dir: Path,
    options: list[str],
) -> None:
    """A command that could reuse another npm cache is refused.

    Args:
        clean_dir: Project without npm state above it.
        options: Runner options lacking lintro's cache.
    """
    reason = npx_pin_refusal(
        cwd=clean_dir,
        package="prettier",
        pin=_PIN,
        environ={},
        command=_npx_command(*options),
    )

    assert_that(reason).contains("does not use lintro's own npm cache")


@pytest.mark.parametrize("delimiter", [":", ";"])
def test_cache_path_with_a_path_delimiter_is_refused(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delimiter: str,
) -> None:
    """A cache path npm would split when it extends PATH is refused.

    Args:
        clean_dir: Project without npm state above it.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped override of lintro's cache root.
        delimiter: PATH delimiter in the cache path.
    """
    monkeypatch.setattr(file_cache, "CACHE_DIR", tmp_path / f"a{delimiter}b")

    reason = npx_pin_refusal(
        cwd=clean_dir,
        package="prettier",
        pin=_PIN,
        environ={},
        command=_npx_command(*npx_runner_options()),
    )

    assert_that(reason).contains("contains a PATH delimiter")


def test_cache_path_that_is_a_file_is_refused(clean_dir: Path) -> None:
    """A cache location that cannot hold npm's cache is refused.

    Args:
        clean_dir: Project without npm state above it.
    """
    npx_cache_dir().parent.mkdir(parents=True, exist_ok=True)
    npx_cache_dir().write_text("", encoding="utf-8")

    reason = npx_pin_refusal(
        cwd=clean_dir,
        package="prettier",
        pin=_PIN,
        environ={},
        command=_npx_command(*npx_runner_options()),
    )

    assert_that(reason).contains("is not a directory")


@pytest.mark.parametrize(
    "environ",
    [
        {"NPM_CONFIG_BIN_LINKS": "false"},
        {"npm_config_bin_links": "0"},
        {"npm_config_workspaces": "true"},
        {"npm_config_global": "true"},
        {"npm_config_include_workspace_root": "true"},
        {"npm_config_offline": "true", "npm_config_prefer_offline": "true"},
        {"npm_config_cache": "/elsewhere:/injected"},
    ],
    ids=[
        "bin-links-false",
        "bin-links-zero",
        "workspaces",
        "global",
        "include-root",
        "offline",
        "foreign-cache-env",
    ],
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
        ("npx", "--yes", *npx_runner_options(), "--package"),
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
