"""The gate probes the hoisted binary npx would launch, not npx's pin (#2043).

``npx --package <pkg>@<pin> <bin>`` skips the install when any node in the
workspace tree is exactly ``<pkg>@<pin>``, then runs ``<bin>`` from the first
ancestor ``node_modules/.bin``. In a workspace whose root hoists an older
copy, that older copy runs even though the pin is current. ``bunx`` never
consults the local tree, so its pin stays the version evidence.

Real-runner coverage lives in
``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

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


def _fake_prettier(package_dir: Path, version: str, log: Path) -> Path:
    """Install a fake ``prettier`` package that reports *version*.

    Args:
        package_dir: ``node_modules/prettier`` directory to create.
        version: Version the package and its binary report.
        log: File each invocation of the binary appends its argv to.

    Returns:
        The package's executable.
    """
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        f'{{"name":"prettier","version":"{version}","bin":{{"prettier":"bin"}}}}\n',
        encoding="utf-8",
    )
    binary = package_dir / "bin"
    binary.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nprintf "{version}\\n"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _workspace(root: Path, *, hoisted: str, log: Path) -> tuple[Path, Path]:
    """Build a workspace whose root hoists *hoisted* and nests the pin.

    The workspace package has its own ``package.json`` and no ``.bin``, so
    the project-local walk stops there and resolution reaches the runner.

    Args:
        root: Directory to build the workspace in.
        hoisted: Version of the copy linked from the root ``.bin``.
        log: Invocation log shared by both fake binaries.

    Returns:
        The workspace package directory and the hoisted ``.bin`` entry.
    """
    workspace = root / "ws"
    (workspace / ".git").mkdir(parents=True)
    (workspace / "package.json").write_text(
        '{"name":"ws","private":true,"workspaces":["packages/*"]}\n',
        encoding="utf-8",
    )
    app = workspace / "packages" / "app"
    app.mkdir(parents=True)
    (app / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    hoisted_binary = _fake_prettier(
        workspace / "node_modules" / "prettier",
        hoisted,
        log,
    )
    _fake_prettier(
        workspace / "node_modules" / "other" / "node_modules" / "prettier",
        _PIN,
        log,
    )
    bin_dir = workspace / "node_modules" / ".bin"
    bin_dir.mkdir()
    (bin_dir / "prettier").symlink_to(hoisted_binary)
    return app, bin_dir / "prettier"


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


@pytest.mark.parametrize(
    ("hoisted", "runnable"),
    [("1.0.0", False), (_PIN, True)],
    ids=["hoisted-below-minimum", "hoisted-at-pin"],
)
def test_npx_gate_probes_the_hoisted_binary_npx_would_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hoisted: str,
    runnable: bool,
) -> None:
    """With npx, a reachable hoisted binary is resolved, probed and gated.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped PATH override.
        hoisted: Version of the hoisted copy.
        runnable: Whether that version meets the minimum.
    """
    log = tmp_path / "argv.log"
    app, hoisted_bin = _workspace(tmp_path, hoisted=hoisted, log=log)
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=app)
    verdict = verify_tool_version(PrettierPlugin().definition, cwd=str(app))

    assert_that(snapshot.command).is_equal_to(
        ((hoisted_bin.parent.resolve() / hoisted_bin.name).as_posix(),),
    )
    assert_that(snapshot.version).is_equal_to(hoisted)
    assert_that(snapshot.version_check_passed).is_equal_to(runnable)
    assert_that(log.read_text(encoding="utf-8")).contains("--version")
    if runnable:
        assert_that(verdict).is_none()
    else:
        assert_that(snapshot.probe_error).contains(
            f"below minimum requirement {_MINIMUM}",
        )
        assert verdict is not None
        assert_that(verdict.skipped).is_true()


def test_bunx_keeps_its_pin_as_evidence_over_a_hoisted_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bunx never launches the local tree, so the pin stands and nothing runs.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped PATH override.
    """
    log = tmp_path / "argv.log"
    app, _hoisted_bin = _workspace(tmp_path, hoisted="1.0.0", log=log)
    _only_runner(monkeypatch, "bunx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=app)

    assert_that(snapshot.command[0]).is_equal_to("bunx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    assert_that(log.exists()).is_false()
