"""Where an executed install command puts a tool's executable.

A ``NOT_DISCOVERABLE`` outcome names the directory the installer wrote to, so
the PATH remedy it suggests can actually help (#2026). The directory is derived
from the command that ran, not from the manifest ``install_type``: a ``binary``
or ``pip`` tool may have gone through ``brew install``, an ``npm`` tool through
a project-local add or a global one (#2042).

A directory is named only when it comes from the package manager itself (a
read-only query such as ``brew --prefix``) or from an environment variable the
manager documents, and only when it is a single absolute path free of control
characters. Every other case is reported as unknown. Queries run only when an
install left its tool undiscoverable, at most once per process each, with a
short timeout.
"""

from __future__ import annotations

import functools
import os
import shlex
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
_DISPLAY_NAMES = {"brew": "Homebrew", "python": "pip", "python3": "pip"}
_PYTHONS = ("python",)
#: Read-only queries that print a Node manager's global executable directory.
_NODE_GLOBAL_BIN_QUERIES: dict[str, tuple[str, ...]] = {
    "bun": ("pm", "bin", "-g"),
    "pnpm": ("bin", "-g"),
    "yarn": ("global", "bin"),
}
#: ``uv pip`` / ``pip`` options that send the install somewhere other than the
#: environment lintro can identify.
_RETARGETING_PIP_OPTIONS = frozenset(
    {"--python", "-p", "--system", "--target", "-t", "--prefix", "--root", "--user"},
)
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
def probe_dir(argv: tuple[str, ...]) -> Path | None:
    """Ask a package manager for a directory, once per process.

    Args:
        argv: Read-only query that prints one directory.

    Returns:
        The printed directory, or None when the query fails, times out, or
        prints anything other than a single clean absolute path.
    """
    try:
        proc = subprocess.run(  # nosec B603 - fixed read-only argv, shell=False
            list(argv),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _valid_dir(proc.stdout.rstrip("\r\n"))


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


def _cargo_config_root(start: Path) -> Path | None:
    """Return ``install.root`` from the cargo config files cargo would read.

    Cargo reads ``.cargo/config.toml`` (or ``.cargo/config``) in *start* and
    every parent, then in ``$CARGO_HOME``; the deepest file that sets a key
    wins. A relative ``install.root`` is relative to the directory holding
    the ``.cargo`` directory.

    Args:
        start: Directory cargo ran in.

    Returns:
        The configured install root, or None when no file sets it.

    Raises:
        _UnknownDestinationError: When a config file cannot be parsed or sets
            a root that is not a clean path.
    """
    home = _cargo_home()
    candidates = [base / ".cargo" for base in (start, *start.parents)]
    candidates.append(home)
    for config_dir in candidates:
        for name in ("config.toml", "config"):
            config = config_dir / name
            if not config.is_file():
                continue
            try:
                data = tomllib.loads(config.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise _UnknownDestinationError from exc
            install = data.get("install", {})
            if not isinstance(install, dict):
                raise _UnknownDestinationError
            root = install.get("root")
            if root is None:
                continue
            if not isinstance(root, str) or has_control_characters(root):
                raise _UnknownDestinationError
            return config_dir.parent / root
    return None


def _cargo_install_bin(args: list[str], *, cwd: Path) -> Path:
    """Return where ``cargo install`` puts executables.

    Precedence follows cargo: ``--root``, ``CARGO_INSTALL_ROOT``, the
    ``install.root`` config value, then ``$CARGO_HOME`` (``~/.cargo``).

    Args:
        args: ``cargo`` arguments after the program name.
        cwd: Directory cargo ran in.

    Returns:
        ``<root>/bin``.

    Raises:
        _UnknownDestinationError: When ``--root`` is not a clean path.
    """
    root_arg = _option_value(args, "--root")
    if root_arg is not None:
        if not root_arg or has_control_characters(root_arg):
            raise _UnknownDestinationError
        return (cwd / root_arg) / "bin"
    root = _env_path("CARGO_INSTALL_ROOT") or _cargo_config_root(cwd) or _cargo_home()
    return root / "bin"


def _venv_scripts(venv: Path) -> Path:
    """Return the executable directory of a virtual environment.

    Args:
        venv: Environment root.

    Returns:
        ``Scripts`` on Windows, ``bin`` elsewhere.
    """
    return venv / ("Scripts" if _IS_WINDOWS else "bin")


def _uv_pip_scripts(args: list[str], *, cwd: Path) -> Path:
    """Return the scripts directory of the environment ``uv pip`` targets.

    uv installs into ``$VIRTUAL_ENV``, then ``$CONDA_PREFIX``, then the nearest
    ``.venv`` in the working directory or a parent.

    Args:
        args: ``uv`` arguments after the program name.
        cwd: Directory uv ran in.

    Returns:
        The target environment's executable directory.

    Raises:
        _UnknownDestinationError: When an option retargets the install or no
            environment can be identified.
    """
    if _RETARGETING_PIP_OPTIONS.intersection(arg.split("=", 1)[0] for arg in args):
        raise _UnknownDestinationError
    env = _env_path("VIRTUAL_ENV") or _env_path("CONDA_PREFIX")
    if env is not None:
        return _venv_scripts(env)
    for base in (cwd, *cwd.parents):
        if (base / ".venv" / "pyvenv.cfg").is_file():
            return _venv_scripts(base / ".venv")
    raise _UnknownDestinationError


def _python_m_pip_scripts(python: str, args: list[str]) -> Path:
    """Ask the interpreter of ``python -m pip install`` for its scripts dir.

    Args:
        python: Interpreter as written in the command.
        args: Arguments after ``-m pip``.

    Returns:
        That interpreter's scripts directory.

    Raises:
        _UnknownDestinationError: When an option retargets the install.
    """
    if _RETARGETING_PIP_OPTIONS.intersection(arg.split("=", 1)[0] for arg in args):
        raise _UnknownDestinationError
    return _probe(
        (python, "-c", "import sysconfig; print(sysconfig.get_path('scripts'))"),
    )


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
        return InstallDestination("rustup", _cargo_home() / "bin")
    if program in ("bun", "npm", "pnpm", "yarn"):
        return InstallDestination(program, _node_destination(argv[0], args, cwd=cwd))
    if program == "uv" and args[:1] == ["tool"]:
        return InstallDestination("uv tool", _probe((argv[0], "tool", "dir", "--bin")))
    if program == "uv" and args[:1] == ["pip"]:
        return InstallDestination("uv pip", _uv_pip_scripts(args, cwd=workdir))
    if program == "pipx":
        bin_dir = _probe((argv[0], "environment", "--value", "PIPX_BIN_DIR"))
        return InstallDestination("pipx", bin_dir)
    if program.startswith(_PYTHONS) and args[:2] == ["-m", "pip"]:
        return InstallDestination("pip", _python_m_pip_scripts(argv[0], args[2:]))
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
