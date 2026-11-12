"""The npx-only gate follows npx's own decision, offline (#2043).

``npx --package <pkg>@<pin> <bin>`` skips its install only when the pin is
already installed somewhere in npm's project tree. It then runs ``<bin>`` by
name from the nearest ancestor ``node_modules/.bin``, which may be another
version, or exits 127 when there is none. Without an installed pin it runs
the pin. ``bunx`` never consults the local tree.

These tests use shell-script stand-ins. Real-runner coverage is in
``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_min_version, get_tool_version
from lintro.enums.tool_name import ToolName
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.tools.core import snapshots
from lintro.tools.prettier.definition import PrettierPlugin

_PIN = get_tool_version("prettier") or ""
_MINIMUM = get_min_version(ToolName.PRETTIER)
_OLDER = "1.0.0"


def _package(directory: Path, version: str, log: Path) -> Path:
    """Install a fake ``prettier`` package whose binary reports *version*.

    Args:
        directory: Package directory to create.
        version: Version the package and its binary report.
        log: File each invocation of the binary appends its argv to.

    Returns:
        The package's executable.
    """
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(
        json.dumps({"name": "prettier", "version": version}),
        encoding="utf-8",
    )
    binary = directory / "bin"
    binary.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nprintf "{version}\\n"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _link_bin(bin_dir: Path, binary: Path) -> Path:
    """Link *binary* as ``<bin_dir>/prettier``.

    Args:
        bin_dir: ``node_modules/.bin`` directory.
        binary: Executable to link.

    Returns:
        The ``.bin`` entry.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    entry = bin_dir / "prettier"
    entry.symlink_to(binary)
    return entry


def _project(root: Path) -> Path:
    """Create a git workspace with one member package.

    Args:
        root: Workspace root to create.

    Returns:
        The member package directory, which has no ``node_modules``.
    """
    (root / ".git").mkdir(parents=True)
    (root / "package.json").write_text(
        '{"name":"ws","private":true,"workspaces":["packages/*"]}\n',
        encoding="utf-8",
    )
    app = root / "packages" / "app"
    app.mkdir(parents=True)
    (app / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    return app


def _layout(tmp_path: Path, name: str, log: Path) -> tuple[Path, Path | None]:
    """Build one of the review's four layouts.

    Args:
        tmp_path: Directory to build in.
        name: Layout identifier.
        log: Invocation log for every fake binary.

    Returns:
        The directory lintro checks, and the ``.bin`` entry npx would run (or
        None when npx would run the pin or nothing).
    """
    if name == "outside-bin-no-pin":
        home = tmp_path / "home"
        outside = _package(home / "node_modules" / "prettier", _OLDER, log)
        _link_bin(home / "node_modules" / ".bin", outside)
        project = home / "proj"
        (project / ".git").mkdir(parents=True)
        (project / "package.json").write_text('{"name":"proj"}\n', encoding="utf-8")
        return project, None
    workspace = tmp_path / "ws"
    app = _project(workspace)
    modules = workspace / "node_modules"
    if name == "nested-pin-no-bin":
        wrapper = modules / "wrapper"
        nested = _package(wrapper / "node_modules" / "prettier", _PIN, log)
        _link_bin(wrapper / "node_modules" / ".bin", nested)
        return app, None
    hoisted = _package(modules / "prettier", _OLDER, log)
    entry = _link_bin(modules / ".bin", hoisted)
    if name == "hoisted-older-pin-elsewhere":
        _package(modules / "other" / "node_modules" / "prettier", _PIN, log)
        return app, entry
    return app, None


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


def test_npx_runs_the_hoisted_binary_when_the_pin_is_installed_elsewhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pin in the tree makes npx run the hoisted older copy; it is refused.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped PATH override.
    """
    log = tmp_path / "argv.log"
    app, entry = _layout(tmp_path, "hoisted-older-pin-elsewhere", log)
    assert entry is not None
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=app)
    verdict = verify_tool_version(PrettierPlugin().definition, cwd=str(app))

    assert_that(snapshot.command).is_equal_to(
        ((entry.parent.resolve() / entry.name).as_posix(),),
    )
    assert_that(snapshot.version).is_equal_to(_OLDER)
    assert_that(snapshot.version_check_passed).is_false()
    assert_that(snapshot.probe_error).contains(f"below minimum requirement {_MINIMUM}")
    assert_that(log.read_text(encoding="utf-8")).contains("--version")
    assert verdict is not None
    assert_that(verdict.skipped).is_true()


@pytest.mark.parametrize(
    "layout",
    ["hoisted-older-no-pin", "outside-bin-no-pin"],
)
def test_npx_keeps_the_pin_when_no_installed_package_matches_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    layout: str,
) -> None:
    """Without the pin in the tree npx installs it; local bins are not used.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped PATH override.
        layout: Layout identifier.
    """
    log = tmp_path / "argv.log"
    project, _entry = _layout(tmp_path, layout, log)
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=project)
    verdict = verify_tool_version(PrettierPlugin().definition, cwd=str(project))

    assert_that(snapshot.command[0]).is_equal_to("npx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    assert_that(log.exists()).is_false()
    assert_that(verdict).is_none()


def test_npx_with_the_pin_installed_but_no_reachable_bin_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When npx would exit 127, the gate refuses instead of passing on the pin.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped PATH override.
    """
    log = tmp_path / "argv.log"
    app, _entry = _layout(tmp_path, "nested-pin-no-bin", log)
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=app)
    verdict = verify_tool_version(PrettierPlugin().definition, cwd=str(app))

    assert_that(snapshot.available).is_false()
    assert_that(snapshot.version).is_none()
    assert_that(snapshot.probe_error).contains(
        f"npx would find prettier@{_PIN} installed",
    ).contains("but no prettier on its PATH")
    assert_that(log.exists()).is_false()
    assert verdict is not None
    assert_that(verdict.unavailable_reason).contains("no prettier on its PATH")


def test_bunx_keeps_its_pin_as_evidence_over_a_hoisted_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With bunx, the pin stands and nothing in the local tree runs.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped PATH override.
    """
    log = tmp_path / "argv.log"
    app, _entry = _layout(tmp_path, "hoisted-older-pin-elsewhere", log)
    _only_runner(monkeypatch, "bunx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=app)

    assert_that(snapshot.command[0]).is_equal_to("bunx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    assert_that(log.exists()).is_false()
