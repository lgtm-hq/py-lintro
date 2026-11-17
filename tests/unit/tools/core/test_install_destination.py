"""NOT_DISCOVERABLE names the directory the executed install command used (#2042).

No package manager runs here: install commands are canned, ``brew --prefix``
and ``which`` are faked, and the assertions read the user-facing message.
"""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired is referenced; no process is spawned
import sysconfig
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_destination import brew_bin_dir
from lintro.tools.core.install_plan import InstallResult
from lintro.tools.core.tool_installer import (
    ToolInstaller,
    is_resolved_command_discoverable,
)
from lintro.tools.core.tool_registry import ManifestRegistry, ManifestTool

_BREW = "/fake/brew/bin/brew"
_BREW_PREFIX = "/fake/homebrew"
_ENV_OVERRIDES = (
    "CARGO_HOME",
    "BUN_INSTALL",
    "PNPM_HOME",
    "UV_TOOL_BIN_DIR",
    "XDG_BIN_HOME",
    "XDG_DATA_HOME",
    "PIPX_BIN_DIR",
)


@dataclass
class _FakeMachine:
    """Stands in for PATH lookups and subprocesses during one install.

    Attributes:
        home: Directory returned by ``Path.home()``.
        npm_dir: Directory holding the ``npm`` executable on PATH.
        brew_prefix_result: What ``brew --prefix`` does: a stdout string,
            a non-zero exit (int), or an exception to raise.
        brew_prefix_calls: Number of ``brew --prefix`` invocations seen.
    """

    home: Path
    npm_dir: Path
    brew_prefix_result: str | int | Exception = _BREW_PREFIX
    brew_prefix_calls: int = field(default=0)

    def which(self, name: str) -> str | None:
        """Resolve only the package managers; the installed tool is absent.

        Args:
            name: Executable name.

        Returns:
            Fake path for ``brew``/``npm``, otherwise None.
        """
        return {"brew": _BREW, "npm": str(self.npm_dir / "npm")}.get(name)

    def run(
        self,
        argv: Sequence[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Answer ``brew --prefix``; every install command exits zero.

        Args:
            argv: Command argv.
            **_kwargs: Ignored subprocess options.

        Returns:
            Completed process.

        Raises:
            result: The configured ``brew --prefix`` failure.
        """
        if list(argv) == [_BREW, "--prefix"]:
            self.brew_prefix_calls += 1
            result = self.brew_prefix_result
            if isinstance(result, Exception):
                raise result
            if isinstance(result, int):
                return subprocess.CompletedProcess(argv, result, "", "boom")
            return subprocess.CompletedProcess(argv, 0, f"{result}\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeMachine]:
    """Isolate HOME, manager env vars, PATH lookups and subprocesses.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        _FakeMachine: The fake machine the installer runs against.
    """
    home = tmp_path / "home"
    fake = _FakeMachine(home=home, npm_dir=tmp_path / "node" / "bin")
    for var in _ENV_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    brew_bin_dir.cache_clear()
    with (
        patch("lintro.tools.core.tool_installer.shutil.which", side_effect=fake.which),
        patch("lintro.tools.core.tool_installer.subprocess.run", side_effect=fake.run),
    ):
        yield fake
    brew_bin_dir.cache_clear()


def _tool(name: str, install_type: str) -> ManifestTool:
    """Build a manifest tool whose bare executable is never on PATH.

    Args:
        name: Tool name.
        install_type: Manifest install type.

    Returns:
        ManifestTool instance.
    """
    return ManifestTool(
        name=name,
        version="1.0.0",
        min_version="1.0.0",
        install_type=install_type,
        install_package=name,
        version_command=(name, "--version"),
    )


def _install(
    tool: ManifestTool,
    command: str,
    *,
    cwd: Path | None = None,
) -> InstallResult:
    """Run *command* for *tool* through the installer with a fixed cwd.

    Args:
        tool: Tool being installed.
        command: Canned install command.
        cwd: Directory the installer would run the command in.

    Returns:
        Classified install result.
    """
    installer = ToolInstaller(ManifestRegistry.load(), RuntimeContext.detect())
    with patch.object(installer, "_install_cwd", return_value=cwd):
        return installer._run_install(tool, command)


@pytest.mark.parametrize(
    ("install_type", "command", "local", "expected"),
    [
        ("binary", "brew install hadolint", False, "brew"),
        ("pip", "brew install semgrep", False, "brew"),
        ("npm", "brew install prettier", True, "brew"),
        ("pip", "uv pip install 'tool>=1.0.0'", False, "scripts"),
        ("pip", "pip install 'tool>=1.0.0'", False, "scripts"),
        ("cargo", "cargo install tool", False, "cargo"),
        ("rustup", "rustup component add clippy", False, "cargo"),
        ("rustup", "rustup toolchain install stable", False, "cargo"),
        ("npm", "npm install -D tool@1.0.0", True, "local"),
        ("npm", "bun install", True, "local"),
        ("npm", "npm install -g tool@1.0.0", False, "npm"),
        ("npm", "bun add -g tool@1.0.0", False, "bun"),
        (
            "binary",
            "bash /x/scripts/utils/install-tools.sh --tools tool",
            False,
            "home",
        ),
        ("pip", "uv tool install tool", False, "home"),
    ],
    ids=[
        "binary-brew",
        "pip-brew",
        "npm-brew-in-project",
        "pip-uv-sysconfig",
        "pip-sysconfig",
        "cargo",
        "rustup-component",
        "rustup-toolchain",
        "npm-project-local",
        "npm-project-restore",
        "npm-global",
        "bun-global",
        "install-tools-script",
        "uv-tool",
    ],
)
def test_message_names_the_directory_the_command_installed_into(
    machine: _FakeMachine,
    tmp_path: Path,
    install_type: str,
    command: str,
    local: bool,
    expected: str,
) -> None:
    """The PATH remedy names the executed command's destination.

    Args:
        machine: Fake PATH lookups and subprocesses.
        tmp_path: Temporary directory provided by pytest.
        install_type: Manifest install type of the tool.
        command: Install command that ran.
        local: Whether the command ran from a Node project root.
        expected: Which destination the message must name.
    """
    project = tmp_path / "project"
    expected_dir = {
        "brew": Path(_BREW_PREFIX) / "bin",
        "scripts": Path(sysconfig.get_path("scripts")),
        "cargo": machine.home / ".cargo" / "bin",
        "local": project / "node_modules" / ".bin",
        "npm": machine.npm_dir,
        "bun": machine.home / ".bun" / "bin",
        "home": machine.home / ".local" / "bin",
    }[expected]

    result = _install(
        _tool("tool", install_type),
        command,
        cwd=project if local else None,
    )

    assert_that(result.outcome).is_equal_to(InstallOutcome.NOT_DISCOVERABLE)
    assert_that(result.message).contains(f"expected under {expected_dir},")
    assert_that(result.message).ends_with(f'export PATH="{expected_dir}:$PATH"')
    if expected != "home":
        assert_that(result.message).does_not_contain(str(machine.home / ".local"))


@pytest.mark.parametrize(
    ("command", "brew_prefix_result", "manager"),
    [
        ("brew install hadolint", 1, "Homebrew"),
        ("brew install hadolint", "", "Homebrew"),
        ("brew install hadolint", subprocess.TimeoutExpired("brew", 5), "Homebrew"),
        ("brew install hadolint", OSError("no brew"), "Homebrew"),
        ("go install example.com/tool@latest", _BREW_PREFIX, "go"),
        ("yarn global add tool@1.0.0", _BREW_PREFIX, "yarn"),
        ("npm install -D tool@1.0.0", _BREW_PREFIX, "npm"),
    ],
    ids=[
        "brew-prefix-nonzero",
        "brew-prefix-empty",
        "brew-prefix-timeout",
        "brew-prefix-oserror",
        "unknown-manager",
        "yarn-global",
        "node-local-without-project",
    ],
)
def test_unknown_destination_is_stated_not_guessed(
    machine: _FakeMachine,
    command: str,
    brew_prefix_result: str | int | Exception,
    manager: str,
) -> None:
    """When the directory cannot be determined, the message says so.

    Args:
        machine: Fake PATH lookups and subprocesses.
        command: Install command that ran.
        brew_prefix_result: Behaviour of ``brew --prefix``.
        manager: Manager name the message must mention.
    """
    machine.brew_prefix_result = brew_prefix_result

    result = _install(_tool("tool", "binary"), command)

    assert_that(result.outcome).is_equal_to(InstallOutcome.NOT_DISCOVERABLE)
    assert_that(result.message).contains(
        f"lintro could not determine where {manager} installed it",
    )
    assert_that(result.message).does_not_contain("expected under")
    assert_that(result.message).does_not_contain("export PATH")
    assert_that(result.message).does_not_contain(".local")


def test_brew_prefix_is_queried_once_per_run(machine: _FakeMachine) -> None:
    """Several brew installs in one run share a single ``brew --prefix``.

    Args:
        machine: Fake PATH lookups and subprocesses.
    """
    messages = [
        _install(_tool(name, "binary"), f"brew install {name}").message
        for name in ("hadolint", "shellcheck", "actionlint")
    ]

    assert_that(machine.brew_prefix_calls).is_equal_to(1)
    for message in messages:
        assert_that(message).contains(str(Path(_BREW_PREFIX) / "bin"))


def test_cargo_home_overrides_the_default_cargo_bin(
    machine: _FakeMachine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CARGO_HOME`` is where cargo and rustup put executables.

    Args:
        machine: Fake PATH lookups and subprocesses.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cargo_home = tmp_path / "custom-cargo"
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))

    result = _install(_tool("clippy", "rustup"), "rustup component add clippy")

    assert_that(result.message).contains(f"expected under {cargo_home / 'bin'},")
    assert_that(result.message).does_not_contain(str(machine.home))


@pytest.mark.parametrize("exists", [True, False], ids=["present", "missing"])
def test_path_probe_is_discoverable_only_when_the_file_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exists: bool,
) -> None:
    """A path argv0 is a file check, not a wrapper that always passes.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
        exists: Whether the probe file is present.
    """
    monkeypatch.chdir(tmp_path)
    probe = Path("scripts") / "probe.sh"
    if exists:
        probe.parent.mkdir()
        probe.write_text("")

    relative = is_resolved_command_discoverable([str(probe), "--version"])
    absolute = is_resolved_command_discoverable([str(tmp_path / probe)])

    assert_that(relative).is_equal_to(exists)
    assert_that(absolute).is_equal_to(exists)
