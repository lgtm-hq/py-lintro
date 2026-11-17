"""NOT_DISCOVERABLE names the directory the executed install command used (#2042).

No package manager runs here: install commands are canned, every manager
query is answered by a fake, HOME and the working directory are temporary,
and the assertions read the user-facing message.
"""

from __future__ import annotations

import subprocess  # nosec B404 - the real run is kept only to paste a suggested PATH line into sh
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_destination import probe_dir
from lintro.tools.core.install_plan import InstallResult
from lintro.tools.core.tool_installer import (
    ToolInstaller,
    is_resolved_command_discoverable,
)
from lintro.tools.core.tool_registry import ManifestRegistry, ManifestTool

_REAL_RUN = subprocess.run
_PY_SCRIPTS_QUERY = (
    "python3",
    "-c",
    "import sysconfig; print(sysconfig.get_path('scripts'))",
)
_ENV_OVERRIDES = (
    "CARGO_HOME",
    "CARGO_INSTALL_ROOT",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
)

#: Answers a fake manager gives to each read-only query.
_DEFAULT_ANSWERS: dict[tuple[str, ...], str | int | BaseException] = {
    ("brew", "--prefix"): "/fake/homebrew",
    ("npm", "config", "get", "prefix"): "/fake/npm-prefix",
    ("pnpm", "bin", "-g"): "/fake/pnpm-home/bin",
    ("bun", "pm", "bin", "-g"): "/fake/bun/bin",
    ("yarn", "global", "bin"): "/fake/yarn/bin",
    ("uv", "tool", "dir", "--bin"): "/fake/uv-tools/bin",
    ("pipx", "environment", "--value", "PIPX_BIN_DIR"): "/fake/pipx/bin",
    _PY_SCRIPTS_QUERY: "/fake/py/bin",
}


@dataclass
class _FakeMachine:
    """Stands in for PATH lookups and subprocesses during one install.

    Attributes:
        home: Directory returned by ``Path.home()``.
        workdir: Process working directory.
        answers: What each manager query does: stdout text, a non-zero exit
            code (int), or an exception to raise.
        queries: Every manager query seen, in order.
    """

    home: Path
    workdir: Path
    answers: dict[tuple[str, ...], str | int | BaseException] = field(
        default_factory=lambda: dict(_DEFAULT_ANSWERS),
    )
    queries: list[tuple[str, ...]] = field(default_factory=list)

    def run(
        self,
        argv: Sequence[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Answer manager queries; every install command exits zero.

        Args:
            argv: Command argv.
            **_kwargs: Ignored subprocess options.

        Returns:
            Completed process.

        Raises:
            answer: The configured query failure.
        """
        key = tuple(argv)
        if key not in self.answers:
            return subprocess.CompletedProcess(argv, 0, "", "")
        self.queries.append(key)
        answer = self.answers[key]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, int):
            return subprocess.CompletedProcess(argv, answer, "", "boom")
        return subprocess.CompletedProcess(argv, 0, f"{answer}\n", "")


def _which(name: str) -> str | None:
    """Resolve the package managers by name; the installed tool is absent.

    Args:
        name: Executable name.

    Returns:
        *name* for a manager the fake answers for, otherwise None.
    """
    return name if any(name == key[0] for key in _DEFAULT_ANSWERS) else None


@pytest.fixture
def machine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_FakeMachine]:
    """Isolate HOME, cwd, manager env vars, PATH lookups and subprocesses.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        _FakeMachine: The fake machine the installer runs against.
    """
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    fake = _FakeMachine(home=home, workdir=workdir)
    for var in _ENV_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setattr("lintro.tools.core.install_destination._IS_WINDOWS", False)
    probe_dir.cache_clear()
    with (
        patch("lintro.tools.core.tool_installer.shutil.which", side_effect=_which),
        patch("lintro.tools.core.tool_installer.subprocess.run", side_effect=fake.run),
    ):
        yield fake
    probe_dir.cache_clear()


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
    command: str,
    *,
    install_type: str = "binary",
    cwd: Path | None = None,
    name: str = "tool",
) -> InstallResult:
    """Run *command* through the installer with a fixed install cwd.

    Args:
        command: Canned install command.
        install_type: Manifest install type of the tool.
        cwd: Directory the installer would run the command in.
        name: Tool name.

    Returns:
        Classified install result.
    """
    installer = ToolInstaller(ManifestRegistry.load(), RuntimeContext.detect())
    with patch.object(installer, "_install_cwd", return_value=cwd):
        return installer._run_install(_tool(name, install_type), command)


def _assert_names(result: InstallResult, directory: Path | str) -> None:
    """Assert the message names *directory* and a PATH line for it.

    Args:
        result: Install result to inspect.
        directory: Directory the message must name.
    """
    assert_that(result.outcome).is_equal_to(InstallOutcome.NOT_DISCOVERABLE)
    assert_that(result.message).contains(f"expected under {directory},")
    assert_that(result.message).ends_with(f'export PATH={directory}:"$PATH"')


def _assert_unknown(result: InstallResult, where: str) -> None:
    """Assert the message admits the directory is unknown.

    Args:
        result: Install result to inspect.
        where: Expected ``where ...`` clause.
    """
    assert_that(result.outcome).is_equal_to(InstallOutcome.NOT_DISCOVERABLE)
    assert_that(result.message).contains(f"lintro could not determine {where};")
    assert_that(result.message).does_not_contain("expected under")
    assert_that(result.message).does_not_contain("export PATH")


@pytest.mark.parametrize(
    ("install_type", "command", "env", "expected"),
    [
        ("binary", "brew install hadolint", {}, "/fake/homebrew/bin"),
        ("pip", "brew install semgrep", {}, "/fake/homebrew/bin"),
        (
            "pip",
            "uv pip install 'tool>=1'",
            {"VIRTUAL_ENV": "/venvs/a"},
            "/venvs/a/bin",
        ),
        (
            "pip",
            "uv pip install tool",
            {"CONDA_PREFIX": "/conda/env"},
            "/conda/env/bin",
        ),
        ("pip", "python3 -m pip install tool", {}, "/fake/py/bin"),
        ("cargo", "cargo install tool", {}, "{home}/.cargo/bin"),
        ("cargo", "cargo install tool --root /roots/r", {}, "/roots/r/bin"),
        ("cargo", "cargo install --root=rel tool", {}, "{work}/rel/bin"),
        ("cargo", "cargo install tool", {"CARGO_INSTALL_ROOT": "/ir"}, "/ir/bin"),
        ("cargo", "cargo install tool", {"CARGO_HOME": "/ch"}, "/ch/bin"),
        (
            "cargo",
            "cargo install tool --root /r",
            {"CARGO_INSTALL_ROOT": "/ir"},
            "/r/bin",
        ),
        ("rustup", "rustup component add clippy", {}, "{home}/.cargo/bin"),
        (
            "rustup",
            "rustup component add clippy",
            {"CARGO_INSTALL_ROOT": "/ir", "CARGO_HOME": "/ch"},
            "/ch/bin",
        ),
        ("npm", "npm install -g tool@1.0.0", {}, "/fake/npm-prefix/bin"),
        (
            "npm",
            "pnpm add -g tool@1.0.0",
            {"PNPM_HOME": "/fake/pnpm-home"},
            "/fake/pnpm-home/bin",
        ),
        ("npm", "bun add -g tool@1.0.0", {}, "/fake/bun/bin"),
        ("npm", "yarn global add tool@1.0.0", {}, "/fake/yarn/bin"),
        ("pip", "uv tool install tool", {}, "/fake/uv-tools/bin"),
        ("pip", "pipx install tool", {}, "/fake/pipx/bin"),
        (
            "binary",
            "bash /x/scripts/utils/install-tools.sh --tools tool",
            {},
            "{home}/.local/bin",
        ),
    ],
    ids=[
        "binary-brew",
        "pip-brew",
        "uv-pip-virtual-env",
        "uv-pip-conda",
        "python-m-pip",
        "cargo-default",
        "cargo-root",
        "cargo-relative-root",
        "cargo-install-root-env",
        "cargo-home",
        "cargo-root-beats-env",
        "rustup-default",
        "rustup-ignores-install-root",
        "npm-global",
        "pnpm-global",
        "bun-global",
        "yarn-global",
        "uv-tool",
        "pipx",
        "install-tools-script",
    ],
)
def test_message_names_the_directory_the_command_installed_into(
    machine: _FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
    install_type: str,
    command: str,
    env: dict[str, str],
    expected: str,
) -> None:
    """The PATH remedy names the executed command's destination.

    Args:
        machine: Fake PATH lookups and subprocesses.
        monkeypatch: Pytest monkeypatch fixture.
        install_type: Manifest install type of the tool.
        command: Install command that ran.
        env: Manager environment variables to set.
        expected: Directory the message must name.
    """
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    directory = expected.format(home=machine.home, work=machine.workdir)

    result = _install(command, install_type=install_type)

    _assert_names(result, directory)


@pytest.mark.parametrize(
    "command",
    ["npm install -D tool@1.0.0", "bun install", "brew install prettier"],
    ids=["npm-project-local", "npm-project-restore", "brew-inside-project"],
)
def test_project_install_destination_follows_the_command(
    machine: _FakeMachine,
    command: str,
) -> None:
    """A project-local add names node_modules/.bin; brew still names brew.

    Args:
        machine: Fake PATH lookups and subprocesses.
        command: Install command run from the project root.
    """
    project = machine.workdir / "project"
    expected = (
        "/fake/homebrew/bin"
        if command.startswith("brew")
        else f"{project}/node_modules/.bin"
    )

    result = _install(command, install_type="npm", cwd=project)

    _assert_names(result, expected)


def test_windows_npm_global_bin_is_the_prefix_itself(
    machine: _FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows npm links global executables into the prefix, not prefix/bin.

    Args:
        machine: Fake PATH lookups and subprocesses.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr("lintro.tools.core.install_destination._IS_WINDOWS", True)
    machine.answers["npm", "config", "get", "prefix"] = "/fake/appdata/npm"

    result = _install("npm install -g tool@1.0.0", install_type="npm")

    _assert_names(result, "/fake/appdata/npm")


def test_uv_pip_names_the_nearest_project_venv(machine: _FakeMachine) -> None:
    """Without VIRTUAL_ENV, uv targets the nearest .venv above the cwd.

    Args:
        machine: Fake PATH lookups and subprocesses.
    """
    venv = machine.workdir / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    nested = machine.workdir / "pkg" / "sub"
    nested.mkdir(parents=True)

    result = _install("uv pip install tool", install_type="pip", cwd=nested)

    _assert_names(result, f"{venv}/bin")


def test_cargo_config_install_root_is_honoured(machine: _FakeMachine) -> None:
    """``install.root`` in .cargo/config.toml beats CARGO_HOME, relative to it.

    Args:
        machine: Fake PATH lookups and subprocesses.
    """
    config_dir = machine.workdir / ".cargo"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('[install]\nroot = "tools"\n')

    result = _install("cargo install tool", install_type="cargo")

    _assert_names(result, f"{machine.workdir}/tools/bin")


@pytest.mark.parametrize(
    ("command", "answer", "env", "where"),
    [
        ("brew install t", 1, {}, "where Homebrew installed it"),
        ("brew install t", "", {}, "where Homebrew installed it"),
        (
            "brew install t",
            subprocess.TimeoutExpired("brew", 5),
            {},
            "where Homebrew installed it",
        ),
        ("brew install t", OSError("no brew"), {}, "where Homebrew installed it"),
        (
            "brew install t",
            "garbage\n/opt/homebrew\x1b[31m",
            {},
            "where Homebrew installed it",
        ),
        ("brew install t", "opt/homebrew", {}, "where Homebrew installed it"),
        ("npm install -g t", "/a\n/b", {}, "where npm installed it"),
        ("pnpm add -g t", 1, {}, "where pnpm installed it"),
        ("bun add -g t", "relative/bin", {}, "where bun installed it"),
        ("pip install t", None, {}, "where pip installed it"),
        ("python3 -m pip install t", 1, {}, "where pip installed it"),
        ("python3 -m pip install --user t", None, {}, "where pip installed it"),
        ("uv pip install t", None, {}, "where uv installed it"),
        (
            "uv pip install --system t",
            None,
            {"VIRTUAL_ENV": "/v"},
            "where uv installed it",
        ),
        (
            "uv pip install t",
            None,
            {"VIRTUAL_ENV": "venv"},
            "where uv installed it",
        ),
        (
            "rustup component add clippy",
            None,
            {"CARGO_HOME": "/x\x1b[31m"},
            "where rustup installed it",
        ),
        (
            "cargo install t",
            None,
            {"CARGO_INSTALL_ROOT": "relative"},
            "where cargo installed it",
        ),
        ("sudo npm install -g t", None, {}, "where it was installed"),
        ("go install example.com/t@latest", None, {}, "where go installed it"),
        ("npm install -D t@1.0.0", None, {}, "where npm installed it"),
    ],
    ids=[
        "brew-nonzero",
        "brew-empty",
        "brew-timeout",
        "brew-oserror",
        "brew-multiline-escape",
        "brew-relative",
        "npm-multiline",
        "pnpm-fails",
        "bun-relative",
        "bare-pip",
        "python-m-pip-fails",
        "python-m-pip-user",
        "uv-pip-no-env",
        "uv-pip-system",
        "uv-pip-relative-venv",
        "cargo-home-escape",
        "cargo-install-root-relative",
        "sudo-wrapper",
        "unknown-manager",
        "node-local-without-project",
    ],
)
def test_unknown_destination_is_stated_not_guessed(
    machine: _FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    answer: str | int | BaseException | None,
    env: dict[str, str],
    where: str,
) -> None:
    """When the directory cannot be determined, the message says so.

    Args:
        machine: Fake PATH lookups and subprocesses.
        monkeypatch: Pytest monkeypatch fixture.
        command: Install command that ran.
        answer: How the manager answers its query, or None for the default.
        env: Manager environment variables to set.
        where: Expected ``where ...`` clause.
    """
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if answer is not None:
        query = next(key for key in machine.answers if command.startswith(key[0]))
        machine.answers[query] = answer

    result = _install(command)

    _assert_unknown(result, where)
    assert_that(result.message).does_not_contain("\x1b")


def test_unparseable_cargo_config_is_unknown(machine: _FakeMachine) -> None:
    """A cargo config lintro cannot read never yields a guessed root.

    Args:
        machine: Fake PATH lookups and subprocesses.
    """
    config_dir = machine.workdir / ".cargo"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("[install\nroot = \n")

    result = _install("cargo install tool", install_type="cargo")

    _assert_unknown(result, "where cargo installed it")


def test_directory_with_control_characters_gets_no_path_line(
    machine: _FakeMachine,
) -> None:
    """A destination holding a control character is never echoed or quoted.

    Args:
        machine: Fake PATH lookups and subprocesses.
    """
    project = machine.workdir / "proj\x1b]0;owned\x07"

    result = _install("npm install -D tool@1.0.0", install_type="npm", cwd=project)

    _assert_unknown(result, "where npm installed it")
    assert_that(result.message).does_not_contain("\x1b")


def test_suggested_path_line_expands_only_path(
    machine: _FakeMachine,
    tmp_path: Path,
) -> None:
    """Pasting the suggested line runs no command substitution.

    Args:
        machine: Fake PATH lookups and subprocesses.
        tmp_path: Temporary directory provided by pytest.
    """
    hostile = "/tmp/$(printf REVIEW_EXECUTED) `id` 'q'"
    machine.answers["uv", "tool", "dir", "--bin"] = hostile
    result = _install("uv tool install tool", install_type="pip")
    line = result.message.rsplit("for example: ", 1)[1]

    pasted = _REAL_RUN(  # nosec B603 B607 - runs the suggested line in a scratch shell
        ["sh", "-c", f'{line}; printf %s "$PATH"'],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert_that(pasted.stdout).is_equal_to(f"{hostile}:/usr/bin:/bin")


def test_each_manager_is_queried_once_per_run(machine: _FakeMachine) -> None:
    """Repeated installs reuse one answer per manager query.

    Args:
        machine: Fake PATH lookups and subprocesses.
    """
    messages = [
        _install(command, name=name).message
        for name in ("hadolint", "shellcheck")
        for command in (f"brew install {name}", f"npm install -g {name}")
    ]

    assert_that(machine.queries).is_equal_to(
        [("brew", "--prefix"), ("npm", "config", "get", "prefix")],
    )
    assert_that(messages[2]).contains("/fake/homebrew/bin")
    assert_that(messages[3]).contains("/fake/npm-prefix/bin")


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
