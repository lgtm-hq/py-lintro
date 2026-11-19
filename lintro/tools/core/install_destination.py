"""Where an executed install command puts a tool's executable.

A ``NOT_DISCOVERABLE`` outcome names the directory the installer wrote to, so
the PATH remedy it suggests can actually help (#2026). The directory is derived
from the command that ran, not from the manifest ``install_type``: a ``binary``
or ``pip`` tool may have gone through ``brew install``, an ``npm`` tool through
a project-local add or a global one (#2042).

A directory is named only when it comes from the package manager itself (a
read-only query such as ``brew --prefix``) or from an environment variable the
manager documents, and only when it is a single absolute path free of control
characters, and only when it is where the manager really writes. Every other
case is reported as unknown. Queries run only when an install left its tool
undiscoverable, at most once per install batch each, with a short timeout.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import shutil
import subprocess  # nosec B404 - read-only manager queries, shell=False, with a timeout
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

#: Seconds a manager query (``brew --prefix``, ``npm config get prefix``, ...)
#: may take before the destination is reported as unknown.
PROBE_TIMEOUT_SECONDS = 5

#: Programs that run another command; the real installer is not argv[0].
_WRAPPERS = frozenset(
    {"sudo", "doas", "env", "nice", "nohup", "time", "command", "exec", "xargs"},
)
_DISPLAY_NAMES = {"brew": "Homebrew"}
#: Read-only queries that print a Node manager's global executable directory.
_NODE_GLOBAL_BIN_QUERIES: dict[str, tuple[str, ...]] = {
    "bun": ("pm", "bin", "-g"),
    "pnpm": ("bin", "-g"),
    "yarn": ("global", "bin"),
}
#: ``uv pip`` options that send the install somewhere other than the
#: environment ``uv pip list`` reports, or make uv read other configuration.
_RETARGETING_UV_PIP_OPTIONS = frozenset(
    {
        "--python",
        "-p",
        "--system",
        "--target",
        "-t",
        "--prefix",
        "--directory",
        "--project",
        "--config-file",
        "--no-config",
    },
)
#: Environment forms of the uv options above. uv documents no variable for
#: ``--target`` or ``--prefix`` (``UV_TARGET``/``UV_PREFIX`` do not exist).
#: ``UV_WORKING_DIR`` (``--directory``) moves the directory uv discovers the
#: environment from, and uv then logs the environment relative to that
#: directory, so lintro cannot place it. Every other uv variable that can
#: change the choice (``UV_PROJECT``, ``UV_CONFIG_FILE``, ``UV_NO_CONFIG``,
#: ...) is inherited by the ``uv pip list`` query and so is reflected in
#: its answer; ``UV_PROJECT_ENVIRONMENT`` does not affect ``uv pip``.
_RETARGETING_UV_ENV = ("UV_PYTHON", "UV_SYSTEM_PYTHON", "UV_WORKING_DIR")
#: ``cargo install`` options that make cargo read configuration other than
#: ``$CARGO_HOME``'s (``--path`` starts discovery at the package) or override it.
_CARGO_CONFIG_OPTIONS = frozenset({"--path", "--config", "-Z"})
#: Line ``uv pip list -v`` logs for the environment it selected.
_UV_ENVIRONMENT_LINE = re.compile(r"Using Python \S+ environment at: (?P<path>.+)$")
#: Lines uv logs when a ``--target``/``--prefix`` (flag or config) is in force.
_UV_TARGET_MARKERS = ("Using `--target` directory", "Using `--prefix` directory")
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_IS_WINDOWS = os.name == "nt"


class _UnknownDestinationError(Exception):
    """Raised when the destination cannot be determined reliably."""


@dataclass(frozen=True)
class InstallDestination:
    """Directory an install command put its executable in.

    Attributes:
        manager: Name of the program that ran the install (``Homebrew``,
            ``cargo``, ``npm``, ...), or None when not even that is known.
        directory: Destination directory, or None when lintro could not
            determine it.
    """

    manager: str | None
    directory: Path | None


def has_control_characters(text: str) -> bool:
    """Return whether *text* holds characters unsafe to echo or paste.

    Args:
        text: Text to inspect.

    Returns:
        True for control, format (bidi overrides) and line-separator
        characters.
    """
    return any(unicodedata.category(char) in _UNSAFE_CATEGORIES for char in text)


def path_export_line(directory: Path) -> str | None:
    """Return a POSIX shell line that puts *directory* first on PATH.

    The directory is single-quoted, so pasting the line expands only
    ``$PATH``.

    Args:
        directory: Directory to add.

    Returns:
        The line, or None on Windows, where no ``export`` line applies.
    """
    if _IS_WINDOWS:
        return None
    return f'export PATH={shlex.quote(str(directory))}:"$PATH"'


def _valid_dir(value: str) -> Path | None:
    """Return *value* as a directory when it is one clean absolute path.

    Args:
        value: Candidate path text.

    Returns:
        The path, or None when empty, relative or holding control characters
        (which includes a second line of output).
    """
    if not value or has_control_characters(value):
        return None
    path = Path(value)
    return path if path.is_absolute() else None


@functools.cache
def _query(argv: tuple[str, ...], cwd: str | None) -> tuple[str, str] | None:
    """Run a read-only manager query, once per batch for each argv and cwd.

    Args:
        argv: Read-only query.
        cwd: Directory to run it in, or None for the process working directory.

    Returns:
        ``(stdout, stderr)`` of a zero exit, or None when the query fails or
        times out.
    """
    try:
        proc = subprocess.run(  # nosec B603 - fixed read-only argv, shell=False
            list(argv),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            cwd=cwd,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout, proc.stderr


def clear_probe_cache() -> None:
    """Forget every manager answer.

    The installer calls this at the start of each batch, so a long-running
    process (watch mode, the MCP server) never reports a directory from
    before the user changed a prefix or PATH.
    """
    _query.cache_clear()


def probe_dir(argv: tuple[str, ...]) -> Path | None:
    """Ask a package manager for a directory.

    Args:
        argv: Read-only query that prints one directory.

    Returns:
        The printed directory, or None when the query fails, times out, or
        prints anything other than a single clean absolute path.
    """
    answer = _query(argv, None)
    if answer is None:
        return None
    return _valid_dir(answer[0].rstrip("\r\n"))


def _probe(argv: tuple[str, ...]) -> Path:
    """Run :func:`probe_dir`, treating no answer as an unknown destination.

    Args:
        argv: Read-only query.

    Returns:
        The printed directory.

    Raises:
        _UnknownDestinationError: When the query gave no usable answer.
    """
    directory = probe_dir(argv)
    if directory is None:
        raise _UnknownDestinationError
    return directory


def _env_path(name: str) -> Path | None:
    """Read a manager-documented directory variable.

    Args:
        name: Environment variable name.

    Returns:
        The directory, or None when the variable is unset or empty.

    Raises:
        _UnknownDestinationError: When it is set to anything but one clean
            absolute path, since the manager would honour it regardless.
    """
    value = os.environ.get(name)
    if not value:
        return None
    path = _valid_dir(value)
    if path is None:
        raise _UnknownDestinationError
    return path


def _uses_option(args: list[str], options: frozenset[str]) -> bool:
    """Return whether *args* pass any of *options*, in any spelling.

    Matches ``--opt``, ``--opt=value``, ``-o``, a short option with its
    value attached (``-ovalue``, ``-o=value``) and one bundled after other
    short flags (``-qo value``), as cargo and uv both accept. Any letter of
    a short-flag group that names one of *options* counts, even when it may
    belong to another flag's attached value: a false match only makes the
    destination unknown.

    Args:
        args: Command arguments.
        options: Option names, including the dashes.

    Returns:
        True when any argument may be one of the options.
    """
    short = {option[1] for option in options if len(option) == 2}
    for arg in args:
        if arg.split("=", 1)[0] in options:
            return True
        if arg[:1] == "-" and arg[1:2] not in ("", "-") and short & set(arg[1:]):
            return True
    return False


def _option_value(args: list[str], option: str) -> str | None:
    """Return the value of ``--opt value`` or ``--opt=value`` in *args*.

    Args:
        args: Command arguments.
        option: Long option name, including the dashes.

    Returns:
        The value, or None when the option is absent.
    """
    for index, arg in enumerate(args):
        if arg == option and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(f"{option}="):
            return arg.split("=", 1)[1]
    return None


def _cargo_home() -> Path:
    """Return ``$CARGO_HOME``, defaulting to ``~/.cargo``.

    Returns:
        Cargo's home directory.
    """
    return _env_path("CARGO_HOME") or Path.home() / ".cargo"


def _config_file(directory: Path) -> Path | None:
    """Return the cargo config file in *directory*, as cargo picks it.

    Cargo reads the legacy ``config`` file whenever it exists, even next to
    a ``config.toml`` (it warns and uses ``config``).

    Args:
        directory: Directory that may hold ``config`` or ``config.toml``.

    Returns:
        The file cargo reads there, or None when there is none.
    """
    return next(
        (
            directory / name
            for name in ("config", "config.toml")
            if (directory / name).is_file()
        ),
        None,
    )


def _cargo_config_files() -> list[Path]:
    """Return the config files ``cargo install`` reads, nearest first.

    ``cargo install`` ignores project configuration and starts discovery at
    ``$CARGO_HOME``: it reads ``.cargo/config[.toml]`` in ``$CARGO_HOME``
    and in every directory above it, then ``$CARGO_HOME/config[.toml]``. A
    nearer file wins.

    Returns:
        The files that exist, in cargo's precedence order.
    """
    home = _cargo_home()
    candidates = [
        *(_config_file(directory / ".cargo") for directory in (home, *home.parents)),
        _config_file(home),
    ]
    return [path for path in candidates if path is not None]


def _config_install_root(config: Path) -> Path | None:
    """Return ``install.root`` from one cargo config file.

    Args:
        config: Config file to read.

    Returns:
        The configured install root, or None when the file does not set it.

    Raises:
        _UnknownDestinationError: When the file cannot be parsed, includes
            other files, or sets a root that is not a clean absolute path.
            Cargo resolves a relative root differently by version and
            spelling (against the working directory without a trailing
            slash, against the config location with one), so lintro does
            not guess.
    """
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _UnknownDestinationError from exc
    if "include" in data:
        raise _UnknownDestinationError
    install = data.get("install", {})
    if not isinstance(install, dict):
        raise _UnknownDestinationError
    root = install.get("root")
    if root is None:
        return None
    path = _valid_dir(root) if isinstance(root, str) else None
    if path is None:
        raise _UnknownDestinationError
    return path


def _cargo_config_root() -> Path | None:
    """Return ``install.root`` from the configuration ``cargo install`` reads.

    Returns:
        The root set by the nearest config file that sets one, or None when
        no file sets it.
    """
    for config in _cargo_config_files():
        root = _config_install_root(config)
        if root is not None:
            return root
    return None


def _cargo_install_bin(args: list[str], *, cwd: Path) -> Path:
    """Return where ``cargo install`` puts executables.

    Precedence follows cargo: ``--root``, ``CARGO_INSTALL_ROOT``, the
    ``install.root`` value from the configuration discovered at
    ``$CARGO_HOME``, then ``$CARGO_HOME`` (``~/.cargo``).

    Args:
        args: ``cargo`` arguments after the program name.
        cwd: Directory cargo ran in.

    Returns:
        ``<root>/bin``.

    Raises:
        _UnknownDestinationError: When ``--root`` is not a clean path, or an
            option makes cargo read other configuration.
    """
    if _uses_option(args, _CARGO_CONFIG_OPTIONS):
        raise _UnknownDestinationError
    root_arg = _option_value(args, "--root")
    if root_arg is not None:
        if not root_arg or has_control_characters(root_arg):
            raise _UnknownDestinationError
        return (cwd / root_arg) / "bin"
    root = _env_path("CARGO_INSTALL_ROOT") or _cargo_config_root() or _cargo_home()
    return root / "bin"


def _rustup_proxy_dir(rustup: str) -> Path:
    """Return the directory holding the proxies of the rustup that ran.

    ``rustup component add`` puts the component in a toolchain, and the tool
    runs through a proxy next to the rustup executable. rustup-init puts both
    in ``$CARGO_HOME/bin``; Homebrew's keg-only ``rustup`` formula keeps them
    in ``$(brew --prefix rustup)/bin``. Any other layout is unknown.

    Args:
        rustup: Program as written in the command.

    Returns:
        The proxy directory.

    Raises:
        _UnknownDestinationError: When rustup does not resolve, or resolves
            outside both known layouts.
    """
    found = shutil.which(rustup)
    if found is None:
        raise _UnknownDestinationError
    location = Path(found).absolute()
    cargo_bin = _cargo_home() / "bin"
    if location.parent == cargo_bin or _same_dir(location.parent, cargo_bin):
        return cargo_bin
    brew = shutil.which("brew")
    if brew is None:
        raise _UnknownDestinationError
    keg = _probe((brew, "--prefix", "rustup"))
    # brew prints the would-be prefix even when rustup is not installed, so
    # the rustup that ran must really live in that keg.
    if not location.resolve().is_relative_to(keg.resolve()):
        raise _UnknownDestinationError
    return keg / "bin"


def _same_dir(first: Path, second: Path) -> bool:
    """Return whether two paths name the same existing directory.

    Args:
        first: A directory.
        second: Another directory.

    Returns:
        True when both exist and are the same directory.
    """
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _venv_scripts(venv: Path) -> Path:
    """Return the executable directory of a virtual environment.

    Args:
        venv: Environment root.

    Returns:
        ``Scripts`` on Windows, ``bin`` elsewhere.
    """
    return venv / ("Scripts" if _IS_WINDOWS else "bin")


def _uv_pip_scripts(uv: str, args: list[str], *, cwd: Path) -> Path:
    """Return the scripts directory of the environment ``uv pip`` targets.

    uv's choice depends on ``VIRTUAL_ENV`` and ``CONDA_PREFIX`` (each skipped
    when it holds no usable Python), the nearest ``.venv``, and uv's
    configuration files, which can also set ``target`` or ``prefix``. So
    lintro asks uv: ``uv pip list --offline -v``, run in the same directory
    with the same environment, logs the environment it selected and any
    configured target or prefix, and reads nothing from the network.

    Args:
        uv: Program as written in the command.
        args: ``uv`` arguments after the program name.
        cwd: Directory uv ran in.

    Returns:
        The target environment's executable directory.

    Raises:
        _UnknownDestinationError: When an option or variable retargets the
            install, or uv does not name exactly one virtual or conda
            environment.
    """
    if _uses_option(args, _RETARGETING_UV_PIP_OPTIONS):
        raise _UnknownDestinationError
    if any(os.environ.get(name) for name in _RETARGETING_UV_ENV):
        raise _UnknownDestinationError
    answer = _query((uv, "pip", "list", "--offline", "-v"), str(cwd))
    if answer is None:
        raise _UnknownDestinationError
    lines = "\n".join(answer).splitlines()
    if any(marker in line for line in lines for marker in _UV_TARGET_MARKERS):
        raise _UnknownDestinationError
    found = {
        match["path"].rstrip("\r")
        for line in lines
        if (match := _UV_ENVIRONMENT_LINE.search(line)) is not None
    }
    if len(found) != 1:
        raise _UnknownDestinationError
    text = found.pop()
    if has_control_characters(text):
        raise _UnknownDestinationError
    # uv prints the environment relative to its working directory when the
    # environment lies below it.
    env = cwd / text
    if not ((env / "pyvenv.cfg").is_file() or (env / "conda-meta").is_dir()):
        raise _UnknownDestinationError
    return _venv_scripts(env)


def _node_destination(manager: str, args: list[str], *, cwd: Path | None) -> Path:
    """Resolve the destination of a bun/npm/pnpm/yarn command.

    Args:
        manager: ``bun``, ``npm``, ``pnpm`` or ``yarn``, as written.
        args: Arguments after the program name.
        cwd: Directory the command ran in, when lintro chose it.

    Returns:
        Project ``node_modules/.bin`` for a local add, the manager's own
        answer for a global one.

    Raises:
        _UnknownDestinationError: When the location cannot be determined.
    """
    name = Path(manager).name
    is_global = (
        "-g" in args
        or "--global" in args
        or (name == "yarn" and args[:1] == ["global"])
    )
    if not is_global:
        if cwd is None:
            raise _UnknownDestinationError
        return cwd / "node_modules" / ".bin"
    if name == "npm":
        # npm honours npm_config_prefix and every .npmrc; only it can say.
        prefix = _probe((manager, "config", "get", "prefix"))
        return prefix if _IS_WINDOWS else prefix / "bin"
    return _probe((manager, *_NODE_GLOBAL_BIN_QUERIES[name]))


def _resolve(
    argv: list[str],
    *,
    cwd: Path | None,
) -> InstallDestination:
    """Dispatch on the program that ran the install.

    Args:
        argv: Split install command.
        cwd: Directory the command ran in, when lintro chose it.

    Returns:
        Manager and destination; the directory is None when unknown. The
        helpers raise _UnknownDestinationError when a recognised manager's
        destination cannot be determined.
    """
    program = Path(argv[0]).name
    args = argv[1:]
    workdir = cwd if cwd is not None else Path.cwd()

    if program == "brew":
        return InstallDestination(
            _DISPLAY_NAMES[program],
            _probe((argv[0], "--prefix")) / "bin",
        )
    if program == "cargo":
        return InstallDestination("cargo", _cargo_install_bin(args, cwd=workdir))
    if program == "rustup":
        return InstallDestination("rustup", _rustup_proxy_dir(argv[0]))
    if program in ("bun", "npm", "pnpm", "yarn"):
        return InstallDestination(program, _node_destination(argv[0], args, cwd=cwd))
    if program == "uv" and args[:1] == ["tool"]:
        return InstallDestination("uv tool", _probe((argv[0], "tool", "dir", "--bin")))
    if program == "uv" and args[:1] == ["pip"]:
        return InstallDestination(
            "uv pip",
            _uv_pip_scripts(argv[0], args, cwd=workdir),
        )
    if program == "pipx":
        bin_dir = _probe((argv[0], "environment", "--value", "PIPX_BIN_DIR"))
        return InstallDestination("pipx", bin_dir)
    if program.startswith("python") and args[:2] == ["-m", "pip"]:
        # pip falls back to --user when site-packages is not writable, and
        # PIP_USER/PIP_TARGET/PIP_PREFIX or any pip.conf can retarget it;
        # lintro never runs this form itself, so it does not guess.
        return InstallDestination("pip", None)
    if program in ("bash", "sh") and any(
        Path(arg).name == "install-tools.sh" for arg in args
    ):
        # scripts/utils/install-tools.sh installs into ~/.local/bin in the
        # mode lintro runs it in (_install_via_script passes only --tools).
        return InstallDestination("install-tools.sh", Path.home() / ".local" / "bin")
    if program in _WRAPPERS or program in ("bash", "sh"):
        return InstallDestination(None, None)
    # Bare pip/pip3 and anything else: lintro cannot tell which environment
    # or prefix the program wrote to.
    return InstallDestination(program, None)


def install_destination(
    command: str,
    *,
    cwd: Path | None = None,
) -> InstallDestination:
    """Derive where an executed install command put the tool's executable.

    Args:
        command: Install command string exactly as it was run.
        cwd: Directory the command ran in (the Node project root for a
            project-local add), or None for the process working directory.

    Returns:
        The manager that ran and its destination directory; ``directory`` is
        None when lintro cannot tell, and is otherwise a clean absolute path.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = []
    if not argv:
        return InstallDestination(None, None)
    try:
        destination = _resolve(argv, cwd=cwd)
    except _UnknownDestinationError:
        program = Path(argv[0]).name
        return InstallDestination(_DISPLAY_NAMES.get(program, program), None)
    directory = destination.directory
    if directory is not None and _valid_dir(str(directory)) is None:
        return InstallDestination(destination.manager, None)
    return destination
