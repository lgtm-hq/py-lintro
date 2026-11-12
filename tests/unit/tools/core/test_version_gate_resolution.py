"""The version gate and execution share one cwd-anchored resolution (#2043).

The gate probes the binary execution will run, never runs a ``bunx``/``npx``
registry runner for ``--version``, and hands its resolved command to execution
so a ``fix()`` walks ``node_modules`` and ``PATH`` once per ``(tool, cwd)``.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fake subprocess boundary; nothing is executed
from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.tools.core.command_builders import CommandBuilderRegistry
from lintro.tools.prettier.definition import PrettierPlugin
from lintro.tools.vue_tsc.definition import VueTscPlugin

_RUNNERS = {"bunx", "npx"}


@pytest.fixture
def resolutions(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Path | None]]:
    """Record every ``(tool, cwd)`` the command builders resolve.

    Args:
        monkeypatch: Scoped override of the builder registry.

    Returns:
        Mutable list of resolution requests, in call order.
    """
    calls: list[tuple[str, Path | None]] = []
    real = CommandBuilderRegistry.get_command

    def spy(
        tool_name: str,
        tool_name_enum: ToolName | None = None,
        cwd: Path | None = None,
    ) -> list[str]:
        calls.append((tool_name, cwd))
        return real(tool_name, tool_name_enum, cwd)

    monkeypatch.setattr(CommandBuilderRegistry, "get_command", staticmethod(spy))
    return calls


def _project_with_local_prettier(root: Path) -> tuple[Path, Path, Path]:
    """Create a git project whose ``node_modules/.bin/prettier`` logs its argv.

    Args:
        root: Directory to create the project in.

    Returns:
        Project root, the local binary, and the file the binary logs to.
    """
    project = root / "project"
    (project / ".git").mkdir(parents=True)
    (project / "data.json").write_text("{}\n", encoding="utf-8")
    log = root / "argv.log"
    binary = project / "node_modules" / ".bin" / "prettier"
    binary.parent.mkdir(parents=True)
    version = get_min_version(ToolName.PRETTIER)
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s %s\\n" "$0" "$*" >> "{log}"\n'
        f'case "$*" in *--version*) printf "{version}\\n"; exit 0 ;; esac\n'
        'case "$*" in *--check*) printf "[warn] data.json\\n" >&2; exit 1 ;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return project, binary, log


def _hide_from_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hidden: set[str],
    present: dict[str, str] | None = None,
) -> None:
    """Make ``shutil.which`` miss *hidden* names and find *present* ones.

    Args:
        monkeypatch: Scoped override of ``shutil.which``.
        hidden: Bare command names that must not resolve on ``PATH``.
        present: Bare command names mapped to the path they resolve to.
    """
    real_which: Callable[..., str | None] = shutil.which
    found = present or {}

    def which(command: str, *args: object, **kwargs: object) -> str | None:
        name = Path(command).name
        if not Path(command).is_absolute() and name in found:
            return found[name]
        if name in hidden:
            return None
        return real_which(command, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)


def test_fix_probes_and_runs_the_project_local_binary_resolved_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolutions: list[tuple[str, Path | None]],
) -> None:
    """The gate's probe and every fix command use one cwd-anchored resolution.

    lintro runs from an unrelated directory, so a gate that ignored the
    execution directory would miss the project's pin.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped working-directory and PATH overrides.
        resolutions: Recorded builder resolutions.
    """
    project, binary, log = _project_with_local_prettier(tmp_path)
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    _hide_from_path(monkeypatch, hidden={"prettier", *_RUNNERS})

    plugin = PrettierPlugin()
    # Config injection writes an atexit-cleaned temp file; it is not under test.
    monkeypatch.setattr(plugin, "_build_config_args", list)

    plugin.fix([str(project / "data.json")], {})

    invocations = log.read_text(encoding="utf-8").splitlines()
    assert_that(invocations).is_not_empty()
    assert_that(invocations[0]).ends_with("--version")
    assert_that({line.split(" ", 1)[0] for line in invocations}).is_equal_to(
        {str(binary.resolve())},
    )
    assert_that([line for line in invocations if "--write" in line]).is_not_empty()
    prettier_resolutions = [call for call in resolutions if call[0] == "prettier"]
    assert_that(prettier_resolutions).is_length(1)
    assert_that(Path(str(prettier_resolutions[0][1])).resolve()).is_equal_to(
        project.resolve(),
    )


def test_base_version_gate_honours_the_execution_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_verify_tool_version(cwd=...)`` checks the project's own binary.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped working-directory and PATH overrides.
    """
    project, binary, log = _project_with_local_prettier(tmp_path)
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    _hide_from_path(monkeypatch, hidden={"prettier", *_RUNNERS})

    verdict = PrettierPlugin()._verify_tool_version(cwd=project)

    assert_that(verdict).is_none()
    assert_that(log.read_text(encoding="utf-8")).starts_with(
        f"{binary.resolve()} --version",
    )


def test_version_gate_never_runs_a_registry_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With nothing installed the gate passes on an unknown version, offline.

    Execution keeps the pinned runner: the gate hands it on unchanged.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped PATH and subprocess overrides.
    """
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    runner = tmp_path / "bunx"
    runner.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    runner.chmod(0o755)
    _hide_from_path(
        monkeypatch,
        hidden={"prettier", "npx"},
        present={"bunx": str(runner)},
    )
    probes: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        probes.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="99.0.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    resolved: dict[tuple[str, str], tuple[str, ...]] = {}

    verdict = verify_tool_version(
        PrettierPlugin().definition,
        cwd=str(project),
        resolved_commands=resolved,
    )

    assert_that(verdict).is_none()
    assert_that([argv for argv in probes if Path(argv[0]).name in _RUNNERS]).is_empty()
    command = resolved[("prettier", str(project))]
    assert_that(command[0]).is_equal_to("bunx")
    assert_that(command[1]).starts_with("prettier@")


def test_vue_tsc_definition_does_not_resolve_from_the_process_directory(
    resolutions: list[tuple[str, Path | None]],
) -> None:
    """Building the vue-tsc definition walks no directory and caches nothing.

    Args:
        resolutions: Recorded builder resolutions.
    """
    plugin = VueTscPlugin()

    version_command = plugin.definition.version_command

    assert_that(version_command).is_equal_to(["vue-tsc", "--version"])
    assert_that(resolutions).is_empty()


@pytest.fixture
def runner_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Leave ``bunx`` as the only way to run any Node tool, with no network.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped PATH, local-install and subprocess overrides.

    Returns:
        Project directory with no local Node installs.
    """
    from lintro.tools.core import command_builders

    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    runner = tmp_path / "bunx"
    runner.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    runner.chmod(0o755)

    def which(command: str, *args: object, **kwargs: object) -> str | None:
        return str(runner) if Path(command).name == "bunx" else None

    def refuse_subprocess(argv: list[str], **kwargs: object) -> None:
        raise AssertionError(f"version gate ran a subprocess: {argv}")

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(
        command_builders,
        "find_local_node_binary",
        lambda name, *, start=None: None,
    )
    monkeypatch.setattr(subprocess, "run", refuse_subprocess)
    return project


def _registered_node_tools() -> dict[str, str]:
    """Map every registered Node tool to the minimum its definition declares.

    Returns:
        Registered tool name -> ``definition.min_version`` (may be empty).
    """
    from lintro.enums.tool_name import normalize_tool_name
    from lintro.plugins.discovery import discover_all_tools
    from lintro.plugins.registry import ToolRegistry
    from lintro.tools.core.command_builders import NodeJSBuilder

    # Discovery is lazy and skipped once anything is registered, so a module
    # that imported a plugin directly would otherwise see only that plugin.
    discover_all_tools()
    builder = NodeJSBuilder()
    tools: dict[str, str] = {}
    for name, definition in ToolRegistry.get_definitions().items():
        try:
            enum = normalize_tool_name(name)
        except ValueError:
            continue
        if builder.can_handle(enum):
            tools[name] = definition.min_version or ""
    return tools


def test_every_node_runner_spec_is_an_exact_pin_at_or_above_the_minimum(
    runner_only: Path,
) -> None:
    """Each registered Node tool's runner fallback passes the gate on its pin.

    Args:
        runner_only: Project where only ``bunx`` can run Node tools.
    """
    from lintro.enums.tool_name import normalize_tool_name
    from lintro.tools.core import snapshots
    from lintro.tools.core.command_builders import NodeJSBuilder
    from lintro.tools.core.node_fallback import (
        registry_fallback_spec,
        split_npm_spec,
    )

    tools = _registered_node_tools()
    assert_that({normalize_tool_name(name) for name in tools}).is_equal_to(
        set(NodeJSBuilder().package_names),
    )

    for name, min_version in tools.items():
        snapshot = snapshots.probe_tool(name, search_root=runner_only)
        _package, pinned = split_npm_spec(
            registry_fallback_spec(snapshot.command) or "",
        )
        assert_that(snapshot.command[0]).described_as(name).is_equal_to("bunx")
        assert_that(snapshot.probe_error).described_as(name).is_none()
        assert_that(snapshot.available).described_as(name).is_true()
        assert_that(snapshot.version).described_as(name).is_equal_to(pinned)
        if min_version:
            assert_that(snapshot.min_version).described_as(name).is_equal_to(
                min_version,
            )


@pytest.mark.parametrize(
    ("pin", "reason"),
    [
        ("0.0.1", "is below minimum requirement"),
        (None, "has no version pin"),
        ("^3.0.0", "is not an exact version"),
    ],
    ids=["below-minimum", "unpinned", "range"],
)
def test_runner_without_an_acceptable_pin_is_refused(
    runner_only: Path,
    monkeypatch: pytest.MonkeyPatch,
    pin: str | None,
    reason: str,
) -> None:
    """A runner that would install a rejected or unpinned version never runs.

    Args:
        runner_only: Project where only ``bunx`` can run Node tools.
        monkeypatch: Scoped override of lintro's npm pins.
        pin: Version the runner spec would carry, or None for no pin.
        reason: Text the refusal must name.
    """
    import lintro._tool_versions as tool_versions
    from lintro.tools.core import snapshots

    monkeypatch.setattr(tool_versions, "get_tool_version", lambda _name: pin)

    snapshot = snapshots.probe_tool("prettier", search_root=runner_only)
    verdict = verify_tool_version(
        PrettierPlugin().definition,
        cwd=str(runner_only),
    )

    assert_that(snapshot.available).is_false()
    assert_that(snapshot.version).is_none()
    assert_that(snapshot.probe_error).contains(reason)
    assert_that(verdict).is_not_none()
    assert verdict is not None
    assert_that(verdict.unavailable_reason).contains(reason)


@pytest.mark.parametrize(
    ("pin", "min_version", "expected_version"),
    [
        ("1.2", "", None),
        ("1.x", "", None),
        ("^1.2.3", "", None),
        ("latest", "", None),
        ("1.2.3", "", "1.2.3"),
        ("2.0.0-beta.1", "1.0.0", "2.0.0-beta.1"),
    ],
    ids=[
        "partial",
        "wildcard",
        "range",
        "dist-tag",
        "exact-without-minimum",
        "exact-prerelease",
    ],
)
def test_runner_pin_must_be_an_exact_semver_with_or_without_a_minimum(
    pin: str,
    min_version: str,
    expected_version: str | None,
) -> None:
    """The exact-pin check stands alone; the minimum applies only when set.

    Args:
        pin: Version the runner spec carries.
        min_version: Minimum the tool declares, empty for none.
        expected_version: Pinned version a runnable snapshot reports, or None
            when the pin must be refused.
    """
    from lintro.models.core.tool_snapshot import ToolCapabilities
    from lintro.tools.core import snapshots

    snapshot = snapshots._registry_fallback_snapshot(
        "prettier",
        command=["bunx", f"prettier@{pin}"],
        min_version=min_version,
        capabilities=ToolCapabilities(),
        binary_path="",
        binary_mtime=0.0,
    )

    assert_that(snapshot.version).is_equal_to(expected_version)
    assert_that(snapshot.available).is_equal_to(expected_version is not None)
    if expected_version is None:
        assert_that(snapshot.probe_error).contains("is not an exact version")
    else:
        assert_that(snapshot.probe_error).is_none()
        assert_that(snapshot.version_check_passed).is_true()
