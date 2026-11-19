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
from lintro.tools.core.install_destination import clear_probe_cache
from lintro.tools.core.install_plan import InstallPlan, InstallResult
from lintro.tools.core.tool_installer import (
    ToolInstaller,
    is_resolved_command_discoverable,
)
from lintro.tools.core.tool_registry import ManifestRegistry, ManifestTool

_REAL_RUN = subprocess.run
_UV_PIP_LIST = ("uv", "pip", "list", "--offline", "-v")
_UV_LOGS_DOT_VENV = "DEBUG Using Python 3.14.6 environment at: .venv"
_ENV_OVERRIDES = (
    "CARGO_HOME",
    "CARGO_INSTALL_ROOT",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "UV_PYTHON",
    "UV_SYSTEM_PYTHON",
    "PIP_USER",
    "PIP_TARGET",
    "PIP_PREFIX",
)


@dataclass(frozen=True)
class _Reply:
    """A manager query's output when stderr matters.

    Attributes:
        stdout: Standard output.
        stderr: Standard error (where uv logs with ``-v``).
    """

    stdout: str = ""
    stderr: str = ""


#: Answers a fake manager gives to each read-only query.
_DEFAULT_ANSWERS: dict[tuple[str, ...], str | int | BaseException | _Reply] = {
    ("brew", "--prefix"): "/fake/homebrew",
    ("npm", "config", "get", "prefix"): "/fake/npm-prefix",
    ("pnpm", "bin", "-g"): "/fake/pnpm-home/bin",
    ("bun", "pm", "bin", "-g"): "/fake/bun/bin",
    ("yarn", "global", "bin"): "/fake/yarn/bin",
    ("uv", "tool", "dir", "--bin"): "/fake/uv-tools/bin",
    ("pipx", "environment", "--value", "PIPX_BIN_DIR"): "/fake/pipx/bin",
    # An interpreter's scripts dir is not where pip installs under --user,
    # PIP_* variables or a pip.conf, so lintro must not ask for it.
    (
        "python3",
        "-c",
        "import sysconfig; print(sysconfig.get_path('scripts'))",
    ): "/fake/py/bin",
}


@dataclass
class _FakeMachine:
    """Stands in for PATH lookups and subprocesses during one install.

    Attributes:
        home: Directory returned by ``Path.home()``.
        workdir: Process working directory.
        answers: What each manager query does: stdout text, a ``_Reply``, a
            non-zero exit code (int), or an exception to raise.
        queries: Every manager query seen, in order.
        query_cwds: The ``cwd`` each query ran with, by query.
        paths: What ``shutil.which`` resolves each program to.
    """

    home: Path
    workdir: Path
    answers: dict[tuple[str, ...], str | int | BaseException | _Reply] = field(
        default_factory=lambda: dict(_DEFAULT_ANSWERS),
    )
    queries: list[tuple[str, ...]] = field(default_factory=list)
    query_cwds: dict[tuple[str, ...], str | None] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)

    def run(
        self,
        argv: Sequence[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Answer manager queries; every install command exits zero.

        Args:
            argv: Command argv.
            **kwargs: Subprocess options; ``cwd`` is recorded.

        Returns:
            Completed process.

        Raises:
            answer: The configured query failure.
        """
        key = tuple(argv)
        if key not in self.answers:
            return subprocess.CompletedProcess(argv, 0, "", "")
        self.queries.append(key)
        self.query_cwds[key] = kwargs.get("cwd")
        answer = self.answers[key]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, int):
            return subprocess.CompletedProcess(argv, answer, "", "boom")
        if isinstance(answer, _Reply):
            return subprocess.CompletedProcess(argv, 0, answer.stdout, answer.stderr)
        return subprocess.CompletedProcess(argv, 0, f"{answer}\n", "")

    def which(self, name: str) -> str | None:
        """Resolve programs from :attr:`paths`; the installed tool is absent.

        Args:
            name: Executable name.

        Returns:
            The configured location, or None.
        """
        return self.paths.get(name)


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
    fake.paths = {key[0]: key[0] for key in _DEFAULT_ANSWERS}
    fake.paths["rustup"] = str(home / ".cargo" / "bin" / "rustup")
    for var in _ENV_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setattr("lintro.tools.core.install_destination._IS_WINDOWS", False)
    clear_probe_cache()
    with (
        patch("lintro.tools.core.tool_installer.shutil.which", side_effect=fake.which),
        patch("lintro.tools.core.tool_installer.subprocess.run", side_effect=fake.run),
    ):
        yield fake
    clear_probe_cache()


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
        (
            "rustup",
            "rustup component add clippy",
            {"CARGO_INSTALL_ROOT": "/ir"},
            "{home}/.cargo/bin",
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
        "cargo-default",
        "cargo-root",
        "cargo-relative-root",
        "cargo-install-root-env",
        "cargo-home",
        "cargo-root-beats-env",
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
    """On Windows npm links into the prefix itself, and no POSIX line is shown.

    Args:
        machine: Fake PATH lookups and subprocesses.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr("lintro.tools.core.install_destination._IS_WINDOWS", True)
    machine.answers["npm", "config", "get", "prefix"] = "/fake/appdata/npm"

    result = _install("npm install -g tool@1.0.0", install_type="npm")

    assert_that(result.outcome).is_equal_to(InstallOutcome.NOT_DISCOVERABLE)
    assert_that(result.message).contains("expected under /fake/appdata/npm,")
    assert_that(result.message).ends_with("or add it to PATH")
    assert_that(result.message).does_not_contain("export")


def _make_venv(path: Path) -> Path:
    """Create the marker file uv's virtual environments carry.

    Args:
        path: Environment root.

    Returns:
        *path*.
    """
    path.mkdir(parents=True)
    (path / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return path


def test_uv_pip_names_the_environment_uv_selects(
    machine: _FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uv skips a VIRTUAL_ENV that does not exist; lintro follows uv.

    ``uv pip list -v`` prints the environment relative to the directory it
    ran in, which must be the install's own directory.

    Args:
        machine: Fake PATH lookups and subprocesses.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("VIRTUAL_ENV", str(machine.workdir / "missing"))
    project = machine.workdir / "project"
    venv = _make_venv(project / ".venv")
    machine.answers[_UV_PIP_LIST] = _Reply(
        stderr="DEBUG Failed to inspect Python interpreter\n"
        "DEBUG Using Python 3.14.6 environment at: .venv\n",
    )

    result = _install("uv pip install tool", install_type="pip", cwd=project)

    _assert_names(result, f"{venv}/bin")
    assert_that(machine.query_cwds[_UV_PIP_LIST]).is_equal_to(str(project))


def test_uv_pip_names_an_absolute_conda_environment(machine: _FakeMachine) -> None:
    """A conda environment uv selects is named by its absolute path.

    Args:
        machine: Fake PATH lookups and subprocesses.
    """
    conda = machine.workdir / "conda" / "env"
    (conda / "conda-meta").mkdir(parents=True)
    machine.answers[_UV_PIP_LIST] = _Reply(
        stderr=f"Using Python 3.12.1 environment at: {conda}\n",
    )

    result = _install("uv pip install tool", install_type="pip")

    _assert_names(result, f"{conda}/bin")


@pytest.mark.parametrize(
    ("env", "stderr"),
    [
        # Each variable case logs a usable .venv, so only the variable
        # itself can make the destination unknown.
        ({"UV_PYTHON": "/usr/bin/python3"}, _UV_LOGS_DOT_VENV),
        ({"UV_SYSTEM_PYTHON": "1"}, _UV_LOGS_DOT_VENV),
        # uv logs the environment relative to UV_WORKING_DIR, not the
        # directory the install ran in, which holds a .venv of its own.
        ({"UV_WORKING_DIR": "/other/project"}, _UV_LOGS_DOT_VENV),
        (
            {},
            "DEBUG Using `--target` directory at `/t`\n"
            "DEBUG Using Python 3.14.6 environment at: .venv",
        ),
        (
            {},
            "DEBUG Using `--prefix` directory at `/p`\n"
            "DEBUG Using Python 3.14.6 environment at: .venv",
        ),
        ({}, "Using Python 3.14.6 environment at: not-a-venv"),
        (
            {},
            "Using Python 3.14.6 environment at: .venv\n"
            "Using Python 3.14.6 environment at: other",
        ),
        ({}, "error: No virtual environment found"),
    ],
    ids=[
        "uv-python-env",
        "uv-system-python-env",
        "uv-working-dir-env",
        "configured-target",
        "configured-prefix",
        "not-a-venv",
        "two-environments",
        "no-environment",
    ],
)
def test_uv_pip_destination_uv_does_not_pin_down_is_unknown(
    machine: _FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    stderr: str,
) -> None:
    """Uv variables, configured targets and unclear answers are not guessed.

    ``VIRTUAL_ENV`` is set to a real environment throughout, so a lintro that
    read it directly would name a directory uv did not use.

    Args:
        machine: Fake PATH lookups and subprocesses.
        monkeypatch: Pytest monkeypatch fixture.
        env: uv variables to set.
        stderr: What ``uv pip list -v`` logs.
    """
    monkeypatch.setenv("VIRTUAL_ENV", str(_make_venv(machine.workdir / "active")))
    _make_venv(machine.workdir / ".venv")
    (machine.workdir / "not-a-venv").mkdir()
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    machine.answers[_UV_PIP_LIST] = _Reply(stderr=f"{stderr}\n")

    result = _install("uv pip install tool", install_type="pip")

    _assert_unknown(result, "where uv installed it")


@pytest.mark.parametrize(
    "command",
    [
        "uv pip install -p/opt/py/bin/python tool",
        "uv pip install -p=3.12 tool",
        "uv pip install -qp 3.12 tool",
        "uv pip install -t/opt/target tool",
        "uv pip install --python=3.12 tool",
    ],
    ids=[
        "python-attached",
        "python-equals",
        "python-bundled",
        "target-attached",
        "python-long-equals",
    ],
)
def test_uv_pip_retargeting_option_in_any_spelling_is_unknown(
    machine: _FakeMachine,
    command: str,
) -> None:
    """``-p``/``-t`` with an attached or bundled value retarget uv too.

    uv itself would log the project ``.venv``, so only the option can make
    the destination unknown, and lintro never asks uv.

    Args:
        machine: Fake PATH lookups and subprocesses.
        command: Install command that ran.
    """
    _make_venv(machine.workdir / ".venv")
    machine.answers[_UV_PIP_LIST] = _Reply(stderr=f"{_UV_LOGS_DOT_VENV}\n")

    result = _install(command, install_type="pip")

    _assert_unknown(result, "where uv installed it")
    assert_that(machine.queries).does_not_contain(_UV_PIP_LIST)


def _write_cargo_config(directory: Path, name: str, root: str) -> None:
    """Write a cargo config file that sets ``install.root``.

    Args:
        directory: Directory to write into.
        name: File name, ``config`` or ``config.toml``.
        root: Value for ``install.root``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(f'[install]\nroot = "{root}"\n')


def test_cargo_install_reads_only_cargo_home_config(
    machine: _FakeMachine,
    tmp_path: Path,
) -> None:
    """``cargo install`` ignores a project ``.cargo/config.toml``.

    Args:
        machine: Fake PATH lookups and subprocesses.
        tmp_path: Temporary directory provided by pytest.
    """
    _write_cargo_config(machine.workdir / ".cargo", "config.toml", "/project-root")
    _write_cargo_config(machine.home / ".cargo", "config.toml", f"{tmp_path}/home-root")

    result = _install("cargo install tool", install_type="cargo")

    _assert_names(result, f"{tmp_path}/home-root/bin")


def test_cargo_legacy_config_beats_config_toml(
    machine: _FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With both files in CARGO_HOME, cargo reads the legacy ``config``.

    Args:
        machine: Fake PATH lookups and subprocesses.
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory provided by pytest.
    """
    cargo_home = tmp_path / "cargo-home"
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    _write_cargo_config(cargo_home, "config.toml", f"{tmp_path}/from-toml")
    _write_cargo_config(cargo_home, "config", f"{tmp_path}/from-legacy")

    result = _install("cargo install tool", install_type="cargo")

    _assert_names(result, f"{tmp_path}/from-legacy/bin")
    assert_that(machine.queries).is_empty()


@pytest.mark.parametrize(
    ("configs", "expected"),
    [
        ({"ch/config.toml": "home", "x/.cargo/config.toml": "above"}, "above"),
        (
            {
                "ch/config.toml": "home",
                "x/.cargo/config.toml": "above",
                "x/ch/.cargo/config": "nearest",
            },
            "nearest",
        ),
        ({"ch/config.toml": "home", "x/.cargo/config.toml": None}, "home"),
    ],
    ids=["parent-beats-home", "home-dot-cargo-is-nearest", "parent-without-root"],
)
def test_cargo_config_above_cargo_home_wins_as_in_cargo(
    machine: _FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configs: dict[str, str | None],
    expected: str,
) -> None:
    """Cargo reads ``.cargo/config*`` from CARGO_HOME upward; nearer wins.

    ``ch`` is ``$CARGO_HOME`` (``<tmp>/x/ch``); the other keys are relative
    to ``<tmp>``. A value of None writes a file that sets no install root.

    Args:
        machine: Fake PATH lookups and subprocesses.
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory provided by pytest.
        configs: Config files to write and the root each one names.
        expected: Root whose ``bin`` the message must name.
    """
    cargo_home = tmp_path / "x" / "ch"
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    for relative, root in configs.items():
        path = (
            cargo_home / relative.removeprefix("ch/")
            if relative.startswith("ch/")
            else tmp_path / relative
        )
        if root is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[build]\njobs = 1\n")
        else:
            _write_cargo_config(path.parent, path.name, f"{tmp_path}/{root}")

    result = _install("cargo install tool", install_type="cargo")

    _assert_names(result, f"{tmp_path}/{expected}/bin")
    assert_that(machine.queries).is_empty()


@pytest.mark.parametrize(
    ("command", "root"),
    [
        ("cargo install tool", "relative/"),
        ("cargo install --path . tool", "/abs"),
        ("cargo install --config install.root='/x' tool", "/abs"),
        ("cargo install --config=install.root='/x' tool", "/abs"),
        ("cargo install -Zunstable-options tool", "/abs"),
        ("cargo install -Zfoo=bar tool", "/abs"),
        ("cargo -Zfoo install tool", "/abs"),
    ],
    ids=[
        "relative-root",
        "path-install",
        "config-override",
        "config-override-attached",
        "unstable-flag-attached",
        "unstable-flag-attached-value",
        "unstable-flag-before-subcommand",
    ],
)
def test_cargo_root_lintro_cannot_pin_down_is_unknown(
    machine: _FakeMachine,
    command: str,
    root: str,
) -> None:
    """A relative root, a ``--path`` install or ``--config`` is not guessed.

    Args:
        machine: Fake PATH lookups and subprocesses.
        command: Install command that ran.
        root: ``install.root`` in ``$CARGO_HOME/config.toml``.
    """
    _write_cargo_config(machine.home / ".cargo", "config.toml", root)

    result = _install(command, install_type="cargo")

    _assert_unknown(result, "where cargo installed it")


def test_homebrew_rustup_names_its_keg(
    machine: _FakeMachine,
    tmp_path: Path,
) -> None:
    """Keg-only Homebrew rustup keeps its proxies in the keg's bin.

    Args:
        machine: Fake PATH lookups and subprocesses.
        tmp_path: Temporary directory provided by pytest.
    """
    cellar = tmp_path / "Cellar" / "rustup" / "1.28.2"
    (cellar / "bin").mkdir(parents=True)
    (cellar / "bin" / "rustup").write_text("")
    opt = tmp_path / "opt" / "rustup"
    opt.parent.mkdir()
    opt.symlink_to(cellar)
    machine.paths["rustup"] = str(opt / "bin" / "rustup")
    machine.answers["brew", "--prefix", "rustup"] = str(opt)

    result = _install("rustup component add rustfmt", install_type="rustup")

    _assert_names(result, f"{opt}/bin")


@pytest.mark.parametrize(
    ("rustup_path", "brew_prefix"),
    [
        (None, "/opt/homebrew/opt/rustup"),
        ("/usr/bin/rustup", "/opt/homebrew/opt/rustup"),
        ("/usr/bin/rustup", 1),
    ],
    ids=["not-on-path", "outside-keg", "brew-fails"],
)
def test_rustup_outside_known_layouts_is_unknown(
    machine: _FakeMachine,
    rustup_path: str | None,
    brew_prefix: str | int,
) -> None:
    """A rustup in neither CARGO_HOME/bin nor its Homebrew keg is not guessed.

    Args:
        machine: Fake PATH lookups and subprocesses.
        rustup_path: Where ``rustup`` resolves, or None.
        brew_prefix: ``brew --prefix rustup`` answer.
    """
    if rustup_path is None:
        del machine.paths["rustup"]
    else:
        machine.paths["rustup"] = rustup_path
    machine.answers["brew", "--prefix", "rustup"] = brew_prefix

    result = _install("rustup component add rustfmt", install_type="rustup")

    _assert_unknown(result, "where rustup installed it")


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
        ("python3 -m pip install t", None, {}, "where pip installed it"),
        (
            "python3 -m pip install t",
            None,
            {"PIP_USER": "1", "PIP_TARGET": "/t", "PIP_PREFIX": "/p"},
            "where pip installed it",
        ),
        ("uv pip install t", None, {}, "where uv installed it"),
        (
            "uv pip install --system t",
            None,
            {"VIRTUAL_ENV": "/v"},
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
        "python-m-pip",
        "python-m-pip-env",
        "uv-pip-no-env",
        "uv-pip-system",
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


@pytest.mark.parametrize(
    "content",
    ["[install\nroot = \n", 'include = "more.toml"\n[install]\nroot = "/abs"\n'],
    ids=["unparseable", "includes-another-file"],
)
def test_unreadable_cargo_config_is_unknown(
    machine: _FakeMachine,
    content: str,
) -> None:
    """A cargo config lintro cannot read in full never yields a guessed root.

    Args:
        machine: Fake PATH lookups and subprocesses.
        content: Text of ``$CARGO_HOME/config.toml``.
    """
    config_dir = machine.home / ".cargo"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(content)

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


def test_each_install_batch_asks_the_manager_again(machine: _FakeMachine) -> None:
    """A later batch in the same process sees a prefix changed in between.

    Args:
        machine: Fake PATH lookups and subprocesses.
    """
    installer = ToolInstaller(ManifestRegistry.load(), RuntimeContext.detect())
    plan = InstallPlan(to_install=[(_tool("tool", "binary"), "brew install tool")])

    first = installer.execute(plan)
    machine.answers["brew", "--prefix"] = "/moved/homebrew"
    second = installer.execute(plan)

    _assert_names(first[0], "/fake/homebrew/bin")
    _assert_names(second[0], "/moved/homebrew/bin")


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
