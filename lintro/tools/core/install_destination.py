"""Where an executed install command puts a tool's executable.

A ``NOT_DISCOVERABLE`` outcome names the directory the installer wrote to, so
the PATH remedy it suggests can actually help (#2026). The directory is derived
from the command that ran, not from the manifest ``install_type``: a ``binary``
or ``pip`` tool may have gone through ``brew install``, an ``npm`` tool through
a project-local add or a global one (#2042).

Only cheap, read-only lookups are made: environment variables, ``which`` and a
single cached ``brew --prefix`` with a short timeout. When the directory cannot
be determined the result says so instead of guessing.
"""

from __future__ import annotations

import functools
import os
import shlex
import shutil
import subprocess  # nosec B404 - only `brew --prefix` is run, shell=False, with a timeout
import sysconfig
from dataclasses import dataclass
from pathlib import Path

#: Seconds allowed for ``brew --prefix`` before the destination is reported
#: as unknown. The query is read-only and normally answers in well under a
#: second; the bound only matters on a wedged Homebrew install.
BREW_PREFIX_TIMEOUT_SECONDS = 5

_NODE_MANAGERS = frozenset({"bun", "npm", "pnpm", "yarn"})
_PIP_MANAGERS = frozenset({"pip", "pip3"})


@dataclass(frozen=True)
class InstallDestination:
    """Directory an install command put its executable in.

    Attributes:
        manager: Human-readable name of the program that ran the install
            (``Homebrew``, ``cargo``, ``npm``, ...), used in messages.
        directory: Destination directory, or None when lintro could not
            determine it.
    """

    manager: str
    directory: Path | None


@functools.cache
def brew_bin_dir(brew: str) -> Path | None:
    """Return Homebrew's ``bin`` directory, asking ``brew --prefix`` once.

    Cached per brew executable so a batch of installs costs at most one call.

    Args:
        brew: Path of the ``brew`` executable to ask.

    Returns:
        ``<prefix>/bin``, or None when the query fails, times out, or prints
        nothing.
    """
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [brew, "--prefix"],
            capture_output=True,
            text=True,
            timeout=BREW_PREFIX_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    prefix = proc.stdout.strip()
    if proc.returncode != 0 or not prefix:
        return None
    return Path(prefix) / "bin"


def _env_dir(name: str) -> Path | None:
    """Return the directory named by environment variable *name*, if set.

    Args:
        name: Environment variable name.

    Returns:
        Path of the variable's value, or None when unset or empty.
    """
    value = os.environ.get(name)
    return Path(value) if value else None


def _cargo_bin() -> Path:
    """Return the directory ``cargo install`` and rustup proxies land in.

    Returns:
        ``$CARGO_HOME/bin``, defaulting to ``~/.cargo/bin``.
    """
    cargo_home = _env_dir("CARGO_HOME") or Path.home() / ".cargo"
    return cargo_home / "bin"


def _uv_tool_bin() -> Path:
    """Return the directory ``uv tool install`` links executables into.

    Mirrors uv's documented order: ``UV_TOOL_BIN_DIR``, ``XDG_BIN_HOME``,
    ``$XDG_DATA_HOME/../bin``, then ``~/.local/bin``.

    Returns:
        uv's tool executable directory.
    """
    explicit = _env_dir("UV_TOOL_BIN_DIR") or _env_dir("XDG_BIN_HOME")
    if explicit is not None:
        return explicit
    data_home = _env_dir("XDG_DATA_HOME")
    if data_home is not None:
        return data_home.parent / "bin"
    return Path.home() / ".local" / "bin"


def _node_global_bin(manager: str) -> Path | None:
    """Return a Node manager's global executable directory when cheap to know.

    Args:
        manager: ``bun``, ``npm``, ``pnpm`` or ``yarn``.

    Returns:
        The directory, or None when only running the manager could tell.
    """
    if manager == "bun":
        return (_env_dir("BUN_INSTALL") or Path.home() / ".bun") / "bin"
    if manager == "npm":
        # npm's default global prefix puts executables beside node and npm.
        # Use the PATH entry, not the resolved symlink target.
        npm = shutil.which("npm")
        return Path(npm).parent if npm is not None else None
    if manager == "pnpm":
        return _env_dir("PNPM_HOME")
    return None


def _node_destination(
    argv: list[str],
    *,
    cwd: Path | None,
) -> InstallDestination:
    """Resolve the destination of a bun/npm/pnpm/yarn command.

    Args:
        argv: Split install command; ``argv[0]`` is the manager.
        cwd: Directory the command ran in.

    Returns:
        Project ``node_modules/.bin`` for a local add, the manager's global bin
        for a global one.
    """
    manager = argv[0]
    is_global = (
        "-g" in argv
        or "--global" in argv
        or (manager == "yarn" and argv[1:2] == ["global"])
    )
    if is_global:
        return InstallDestination(manager, _node_global_bin(manager))
    if cwd is None:
        return InstallDestination(manager, None)
    return InstallDestination(manager, cwd / "node_modules" / ".bin")


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
        None when lintro cannot tell.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = []
    if not argv:
        return InstallDestination("the install command", None)

    program = Path(argv[0]).name
    args = argv[1:]

    if program == "brew":
        brew = shutil.which(argv[0])
        directory = brew_bin_dir(brew) if brew is not None else None
        return InstallDestination("Homebrew", directory)
    if program in ("cargo", "rustup"):
        return InstallDestination(program, _cargo_bin())
    if program in _NODE_MANAGERS:
        return _node_destination([program, *args], cwd=cwd)
    if program == "uv" and args[:1] == ["tool"]:
        return InstallDestination("uv tool", _uv_tool_bin())
    if program == "pipx":
        bin_dir = _env_dir("PIPX_BIN_DIR") or Path.home() / ".local" / "bin"
        return InstallDestination("pipx", bin_dir)
    if program in _PIP_MANAGERS or (program == "uv" and args[:1] == ["pip"]):
        return InstallDestination(
            "uv pip" if program == "uv" else program,
            Path(sysconfig.get_path("scripts")),
        )
    if program in ("bash", "sh") and any(
        Path(arg).name == "install-tools.sh" for arg in args
    ):
        # scripts/utils/install-tools.sh: --docker installs system-wide,
        # every other mode into ~/.local/bin.
        if "--docker" in args or "docker" in args:
            return InstallDestination("install-tools.sh", Path("/usr/local/bin"))
        return InstallDestination(
            "install-tools.sh",
            Path.home() / ".local" / "bin",
        )
    return InstallDestination(program, None)
