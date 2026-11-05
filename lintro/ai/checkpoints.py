"""Git-checkpoint snapshots for AI fix (and optional fmt) rollback.

Captures working-tree file state to ``refs/lintro/checkpoints/<run-id>`` using
git plumbing on a temporary index (``GIT_INDEX_FILE``). Never touches the
user's index, stash, or ``HEAD``.

Outside a usable git work tree (no git, bare repo, etc.) callers should fall
back to :mod:`lintro.ai.undo` file-content snapshots.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
import subprocess  # nosec B404 - subprocess invokes git with shell=False; args are plumbing only
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.ai.paths import atomic_write_bytes

CHECKPOINT_REF_PREFIX = "refs/lintro/checkpoints/"
DEFAULT_CHECKPOINT_RETENTION = 10
_GIT_TIMEOUT_SECONDS = 60.0
# Bound process count as well as encoded argv size.
_GIT_PATH_CHUNK = 500
_GIT_ARG_MAX = 128 * 1024
_GIT_ARG_RESERVE = 8 * 1024
# Git tree entry modes that are not plain file content.
_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"
_TREE_MODE = "040000"
_REGULAR_MODES = ("100644", "100755")
# Ambient git state that must never leak into these plumbing calls.
_INHERITED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)
# Descriptor-relative access binds each symlink check to the file actually
# read or written. Platforms without it keep the path-based behaviour.
# ``os.replace`` (used by ``_write_at``) has no entry of its own in
# ``os.supports_dir_fd``: it shares ``rename``'s dir_fd support, so the
# ``os.rename`` check below gates it too.
_DIR_FD_ACCESS = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NONBLOCK")
    and hasattr(os, "fchmod")
    and {os.open, os.stat, os.readlink, os.unlink, os.rename, os.mkdir, os.symlink}
    <= os.supports_dir_fd
)
_COPY_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Checkpoint:
    """A lintro-managed git tree snapshot.

    Attributes:
        ref: Full ref name under ``refs/lintro/checkpoints/``.
        run_id: Unique run identifier embedded in the ref.
        root: Absolute repository root path.
        paths: Repo-relative paths included in the snapshot.
        tree_sha: Object name of the written tree.
        base: Directory that relative caller paths are resolved against —
            the ``workspace_root`` passed at capture, which for a CLI run is
            the invocation directory, not the repository root.
    """

    ref: str
    run_id: str
    root: Path
    paths: tuple[str, ...] = field(default_factory=tuple)
    tree_sha: str = ""
    base: Path | None = None


class CheckpointError(Exception):
    """Raised when a checkpoint capture, restore, or prune operation fails."""


class _BeyondSymlinkError(CheckpointError):
    """A target's parent directory component is a symbolic link."""


@dataclass
class _TargetSnapshot:
    """Working-tree state of checkpoint targets, read once.

    Attributes:
        modes: Git mode per path, ``0`` for absence, None for unsupported.
        sources: ``hash-object`` operand per regular file: a private staged
            copy of the bytes, or the repo-relative path on the fallback.
        links: Symlink target text per file-symlink path.
    """

    modes: dict[str, str | None] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    links: dict[str, bytes] = field(default_factory=dict)


def _git_bin() -> str | None:
    """Return the resolved git binary path, or None if unavailable."""
    return shutil.which("git")


def _git_env(overlay: dict[str, str] | None) -> dict[str, str]:
    """Build a hardened environment for a git plumbing call.

    Args:
        overlay: Variables to set after sanitising (e.g. the temp index).

    Returns:
        The environment to pass to :func:`subprocess.run`.
    """
    return _scrub_git_env(os.environ, overlay)


def _scrub_git_env(
    base: Mapping[str, str],
    overlay: dict[str, str] | None,
) -> dict[str, str]:
    """Apply the git plumbing environment policy to ``base``.

    Ambient ``GIT_*`` variables are stripped first. When lintro runs inside a
    git hook, git exports ``GIT_DIR``, ``GIT_INDEX_FILE`` and friends; a
    plumbing call that inherited them would target the hook's repository and
    index instead of the one at ``cwd``, breaking this module's promise never
    to touch the user's index.

    Args:
        base: Inherited environment to sanitise; never mutated.
        overlay: Variables to set after sanitising (e.g. the temp index).

    Returns:
        A new environment mapping.
    """
    full_env = dict(base)
    for leaked in _INHERITED_GIT_VARS:
        full_env.pop(leaked, None)
    if overlay:
        full_env.update(overlay)
    # Avoid locale/pager interference and never open an editor.
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    full_env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    # Target paths are literal filenames; ``*``, ``?``, ``[`` and a leading
    # ``:`` in a filename must not be read as pathspec magic.
    full_env["GIT_LITERAL_PATHSPECS"] = "1"
    return full_env


def _run_git(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    check: bool = False,
    binary: bool = False,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run a git command with optional temp-index environment.

    Args:
        args: Git arguments (without the leading ``git``).
        cwd: Working directory.
        env: Optional environment overlay (e.g. ``GIT_INDEX_FILE``).
        check: When True, raise :class:`CheckpointError` on non-zero exit.
        binary: When True, return raw ``bytes`` streams instead of decoded
            text. Required for blob contents, which are not always UTF-8.
        input_bytes: Optional raw stdin data; requires ``binary=True``.

    Returns:
        Completed process whose stdout/stderr are ``str`` (or ``bytes`` when
        ``binary`` is set).

    Raises:
        CheckpointError: When git is missing, times out, or ``check`` fails.
    """
    git_bin = _git_bin()
    if git_bin is None:
        raise CheckpointError("git is not installed or not on PATH")
    try:
        result = subprocess.run(  # nosec B603 - argv is [resolved git binary, *args]; shell=False; plumbing args only
            [git_bin, *args],
            cwd=cwd,
            capture_output=True,
            text=not binary,
            input=input_bytes,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            env=_git_env(env),
        )
    except subprocess.TimeoutExpired as exc:
        raise CheckpointError(
            f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s",
        ) from exc
    except OSError as exc:
        raise CheckpointError(f"Failed to run git {' '.join(args)}: {exc}") from exc
    if check and result.returncode != 0:
        streams = [result.stderr, result.stdout]
        if binary:
            streams = [s.decode("utf-8", errors="replace") for s in streams]
        stderr = streams[0].strip() or streams[1].strip() or "unknown git error"
        raise CheckpointError(f"git {' '.join(args)} failed: {stderr}")
    return result


def git_checkpoints_available(workspace_root: Path) -> bool:
    """Return whether git checkpoints can be used under ``workspace_root``.

    Requires git on ``PATH``, a non-bare work tree, and a readable repo root.

    Args:
        workspace_root: Project directory to probe.

    Returns:
        True when capture/restore via git refs is supported.
    """
    if _git_bin() is None:
        return False
    root = str(workspace_root)
    try:
        inside = _run_git(
            ["rev-parse", "--is-inside-work-tree"],
            cwd=root,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return False
        bare = _run_git(
            ["rev-parse", "--is-bare-repository"],
            cwd=root,
        )
        if bare.returncode == 0 and bare.stdout.strip() == "true":
            return False
    except CheckpointError:
        return False
    return True


def _repo_root(workspace_root: Path) -> Path | None:
    """Resolve the git top-level directory for ``workspace_root``."""
    try:
        result = _run_git(
            ["rev-parse", "--show-toplevel"],
            cwd=str(workspace_root),
        )
    except CheckpointError:
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def _expand_directory(*, root: Path, rel_dir: str) -> list[str]:
    """List candidate files under ``rel_dir`` using git's own path filters.

    ``git ls-files -co --exclude-standard`` returns tracked plus untracked
    files while honouring ``.gitignore``. Walking the directory manually
    instead would drag ``.git/``, ``.venv/`` and every other ignored tree into
    the snapshot. A git failure propagates as :class:`CheckpointError`.

    Args:
        root: Repository root.
        rel_dir: Repo-relative directory (``""`` for the root itself).

    Returns:
        Repo-relative POSIX file paths, or an empty list when no files match.
    """
    args = ["ls-files", "-co", "--exclude-standard", "-z", "--"]
    args.append(rel_dir or ".")
    result = _run_git(args, cwd=str(root), check=True)
    return [entry for entry in result.stdout.split("\0") if entry]


def _normalize_paths(
    paths: list[str] | tuple[str, ...],
    *,
    root: Path,
    base: Path | None = None,
) -> list[str]:
    """Convert paths to unique repo-relative POSIX strings.

    Directories are expanded through :func:`_expand_directory` so ignored
    trees never enter a snapshot. Files that do not exist are kept so that a
    restore can delete paths lintro created after capture. Enumeration errors
    propagate as :class:`CheckpointError`; no partial path list is returned.

    Args:
        paths: Absolute or relative path strings (files or directories).
        root: Repository root; results are relative to it.
        base: Directory relative inputs are resolved against. Defaults to
            ``root``, but a CLI run passes its invocation directory — running
            ``lintro fmt src/app.py`` from a subdirectory must not snapshot
            ``<repo>/src/app.py``.

    Returns:
        Sorted unique repo-relative POSIX paths.
    """
    root_resolved = root.resolve()
    base_resolved = base.resolve() if base is not None else root_resolved
    rels: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        candidate = Path(raw)
        raw_abs = candidate if candidate.is_absolute() else base_resolved / candidate
        raw_abs = Path(os.path.normpath(raw_abs))
        # Resolve the parent, never the leaf: resolving the whole path would
        # rewrite a symlinked target to whatever it points at, and the
        # checkpoint would then snapshot (and restore) the wrong file.
        abs_path = raw_abs.parent.resolve() / raw_abs.name
        try:
            rel = abs_path.relative_to(root_resolved)
        except ValueError:
            logger.debug("Skipping path outside repo for checkpoint: {}", raw)
            continue
        if abs_path.is_dir() and not abs_path.is_symlink():
            rels.update(_expand_directory(root=root_resolved, rel_dir=rel.as_posix()))
        else:
            # Include missing paths so restore can delete files created later;
            # capture only adds existing files to the temp index.
            rels.add(rel.as_posix())
    return sorted(rels)


def _new_run_id() -> str:
    """Return a sortable unique run id (timestamp + short uuid)."""
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _beyond_symlink(root: Path, rel: str) -> bool:
    """Return whether an existing ancestor of ``rel`` below ``root`` is a symlink.

    Git refuses such a path ("beyond a symbolic link") because reading or
    writing it would follow the link — possibly out of the repository.

    Args:
        root: Repository root.
        rel: Repo-relative POSIX path.

    Returns:
        True when any parent directory component is a symbolic link.
    """
    current = root
    for part in Path(rel).parts[:-1]:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except (FileNotFoundError, NotADirectoryError):
            return False
    return False


def _raw_file_mode(path: Path) -> str | None:
    """Return disk Git mode, ``0`` for absence, or None for unsupported targets."""
    try:
        mode = path.lstat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return "0"
    if stat.S_ISREG(mode):
        return "100755" if mode & stat.S_IXUSR else "100644"
    if stat.S_ISLNK(mode):
        try:
            return _SYMLINK_MODE if stat.S_ISREG(path.stat().st_mode) else None
        except (FileNotFoundError, NotADirectoryError):
            return None
    return None


def _open_root(root: Path) -> int:
    """Open the repository root directory for descriptor-relative access.

    Args:
        root: Repository root.

    Returns:
        A directory descriptor the caller must close.
    """
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY)


def _descend(parent: int, part: str, *, create: bool) -> int | None:
    """Open one directory component below ``parent`` without following links.

    Args:
        parent: Descriptor of the directory holding ``part``.
        part: A single path component.
        create: Create ``part`` when missing (restore).

    Returns:
        The component's descriptor, or None when it is absent or not a
        directory and ``create`` is False.

    Raises:
        CheckpointError: When ``part`` would leave the repository.
        _BeyondSymlinkError: When ``part`` is a symbolic link.
        FileNotFoundError: When a directory to create vanished again.
        OSError: When ``part`` cannot be created or opened.
    """
    if part in (os.sep, os.curdir, os.pardir):
        raise CheckpointError(f"Invalid checkpoint path component: {part!r}")
    if create:
        with contextlib.suppress(FileExistsError):
            os.mkdir(part, dir_fd=parent)
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        return os.open(part, flags, dir_fd=parent)
    except FileNotFoundError:
        if create:
            raise
        return None
    except OSError as exc:
        # Linux reports ENOTDIR, macOS ELOOP and FreeBSD EMLINK for a link.
        if exc.errno not in (errno.ENOTDIR, errno.ELOOP, errno.EMLINK):
            raise
        info = os.stat(part, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise _BeyondSymlinkError(part) from exc
        if create:
            raise
        return None


def _open_parent(root_fd: int, rel: str, *, create: bool) -> int | None:
    """Open the directory holding ``rel`` through no-follow descriptors.

    Each component is opened relative to the one before it, so the directory
    returned is the one that was checked, whatever later happens to the
    pathname.

    Args:
        root_fd: Repository root descriptor; left open.
        rel: Repo-relative POSIX path.
        create: Create missing directories (restore).

    Returns:
        A descriptor the caller must close, or None when an ancestor is
        absent or not a directory and ``create`` is False.

    Raises:
        _BeyondSymlinkError: When an ancestor is a symbolic link.
    """
    current = os.dup(root_fd)
    try:
        for part in Path(rel).parts[:-1]:
            child = _descend(current, part, create=create)
            os.close(current)
            current = -1
            if child is None:
                return None
            current = child
        opened, current = current, -1
        return opened
    except _BeyondSymlinkError as exc:
        raise _BeyondSymlinkError(
            f"Checkpoint target is beyond a symbolic link: {rel}",
        ) from exc
    finally:
        if current >= 0:
            os.close(current)


def _copy_regular(parent: int, name: str, staged: Path) -> str | None:
    """Copy a regular file's exact bytes from a no-follow descriptor.

    Args:
        parent: Pinned descriptor of the directory holding ``name``.
        name: Final path component.
        staged: Private destination for the bytes.

    Returns:
        The Git mode from ``fstat`` of the opened file, or None when it is
        no longer a regular file.
    """
    # ``O_NONBLOCK`` keeps a FIFO swapped in after ``lstat`` from blocking.
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode):
            return None
        with staged.open("xb") as out:
            while chunk := os.read(source, _COPY_CHUNK):
                out.write(chunk)
    finally:
        os.close(source)
    return "100755" if info.st_mode & stat.S_IXUSR else "100644"


def _read_target(
    root_fd: int,
    rel: str,
    staged: Path,
) -> tuple[str | None, bytes | None]:
    """Read one target's state through pinned no-follow descriptors.

    Args:
        root_fd: Repository root descriptor; left open.
        rel: Repo-relative POSIX path.
        staged: Private destination for a regular file's bytes.

    Returns:
        ``(Git mode, symlink target text)``; the text is set only for a file
        symlink.
    """
    parent = _open_parent(root_fd, rel, create=False)
    if parent is None:
        return "0", None
    name = Path(rel).name
    try:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return "0", None
        if stat.S_ISREG(info.st_mode):
            return _copy_regular(parent, name, staged), None
        if not stat.S_ISLNK(info.st_mode):
            return None, None
        try:
            # Resolved from the pinned directory, as the kernel resolves links.
            if not stat.S_ISREG(os.stat(name, dir_fd=parent).st_mode):
                return None, None
        except (FileNotFoundError, NotADirectoryError):
            return None, None
        return _SYMLINK_MODE, os.fsencode(os.readlink(name, dir_fd=parent))
    finally:
        os.close(parent)


def _git_path_batches(
    paths: list[str],
    *,
    args: list[str],
    env: dict[str, str],
) -> Iterator[list[str]]:
    """Yield literal path operands within a conservative encoded exec budget.

    Args:
        paths: Repo-relative path operands.
        args: Fixed Git arguments before the operands, including ``--``.
        env: Temporary-index environment overlay.

    Yields:
        list[str]: Batches bounded by both count and encoded bytes.

    Raises:
        CheckpointError: When even one target cannot fit the exec budget.
    """
    try:
        arg_max = os.sysconf("SC_ARG_MAX")
    except (AttributeError, OSError, ValueError):
        arg_max = 32 * 1024
    # Cap argv at half macOS's usual 256 KiB limit even on larger-limit hosts.
    # Include NULs and conservative 64-bit argv/environ pointers; reserve extra
    # space for platform overhead and the subprocess implementation.
    budget = arg_max if arg_max > 0 else 32 * 1024
    budget -= _GIT_ARG_RESERVE
    budget -= sum(
        len(os.fsencode(key)) + len(os.fsencode(value)) + 2 + 8
        for key, value in _git_env(env).items()
    )
    budget = min(budget, _GIT_ARG_MAX)
    fixed_size = sum(
        len(os.fsencode(arg)) + 1 + 8 for arg in [_git_bin() or "git", *args]
    )
    size = fixed_size
    chunk: list[str] = []
    for path in paths:
        path_size = len(os.fsencode(path)) + 1 + 8
        if fixed_size + path_size > budget:
            raise CheckpointError("Checkpoint target exceeds Git argument budget")
        if chunk and (len(chunk) >= _GIT_PATH_CHUNK or size + path_size > budget):
            yield chunk
            chunk = []
            size = fixed_size
        chunk.append(path)
        size += path_size
    if chunk:
        yield chunk


def _target_modes(
    *,
    root: Path,
    paths: list[str],
    remove_nonfiles: bool,
    staging: Path,
) -> _TargetSnapshot:
    """Read each target's Git mode and content without following parent symlinks.

    Where the platform supports it, every target is opened through
    descriptors pinned component by component with ``O_NOFOLLOW``, and the
    bytes are copied from that descriptor into ``staging``. The symlink check
    and the read are therefore one access: a parent swapped for a link
    afterwards cannot redirect what is hashed.

    Args:
        root: Repository root.
        paths: Literal repo-relative target paths.
        remove_nonfiles: Diff mode; a path beyond a symlink is reported absent,
            matching Git's own working-tree view, instead of failing.
        staging: Private directory receiving copies of regular-file bytes.

    Returns:
        Modes (``0`` for absence, None for unsupported targets), Git operands
        for regular files, and file-symlink target text.

    Raises:
        CheckpointError: At capture, when a target lies beyond a symlink
            (path-based fallback).
        _BeyondSymlinkError: At capture, when a target lies beyond a symlink.
    """
    snapshot = _TargetSnapshot()
    if not _DIR_FD_ACCESS:
        for rel in paths:
            if _beyond_symlink(root, rel):
                if not remove_nonfiles:
                    raise CheckpointError(
                        f"Checkpoint target is beyond a symbolic link: {rel}",
                    )
                snapshot.modes[rel] = "0"
                continue
            mode = _raw_file_mode(root / rel)
            snapshot.modes[rel] = mode
            if mode in _REGULAR_MODES:
                snapshot.sources[rel] = rel
            elif mode == _SYMLINK_MODE:
                snapshot.links[rel] = os.fsencode(os.readlink(root / rel))
        return snapshot
    root_fd = _open_root(root)
    try:
        for number, rel in enumerate(paths):
            staged = staging / str(number)
            try:
                mode, link = _read_target(root_fd, rel, staged)
            except _BeyondSymlinkError:
                if not remove_nonfiles:
                    raise
                mode, link = "0", None
            snapshot.modes[rel] = mode
            if mode in _REGULAR_MODES:
                snapshot.sources[rel] = str(staged)
            elif link is not None:
                snapshot.links[rel] = link
    finally:
        os.close(root_fd)
    return snapshot


def _ignore_executable_bits(
    *,
    root: Path,
    modes: dict[str, str | None],
    env: dict[str, str],
) -> None:
    """Keep the private index's regular-file modes, as ``core.filemode=false`` does.

    Mirrors Git: a regular file keeps the mode its index entry already has,
    and a file with no regular entry is recorded as ``100644``. A failure to
    list the private index propagates as :class:`CheckpointError`.

    Args:
        root: Repository root.
        modes: Disk modes per path; regular-file entries are updated in place.
        env: Temporary-index environment overlay.
    """
    regular = [rel for rel, mode in modes.items() if mode in _REGULAR_MODES]
    recorded: dict[str, str] = {}
    args = ["ls-files", "--stage", "-z", "--"]
    for chunk in _git_path_batches(regular, args=args, env=env):
        listing = _run_git(
            [*args, *chunk],
            cwd=str(root),
            env=env,
            check=True,
            binary=True,
        ).stdout
        for record in filter(None, listing.split(b"\0")):
            meta, _, name = record.partition(b"\t")
            recorded[os.fsdecode(name)] = meta.split(b" ", 1)[0].decode("ascii")
    for rel in regular:
        prior = recorded.get(rel)
        modes[rel] = prior if prior in _REGULAR_MODES else "100644"


def _hash_regular_files(
    *,
    root: Path,
    snapshot: _TargetSnapshot,
    env: dict[str, str],
) -> list[bytes]:
    """Write regular-file blobs in batches and return their index records.

    Args:
        root: Repository root.
        snapshot: Target state; ``sources`` names each file's Git operand.
        env: Temporary-index environment overlay.

    Returns:
        ``--index-info`` records for the regular-file targets.

    Raises:
        CheckpointError: When Git fails or returns the wrong hash count.
    """
    entries: list[bytes] = []
    regular = [rel for rel, mode in snapshot.modes.items() if mode in _REGULAR_MODES]
    owners = {snapshot.sources[rel]: rel for rel in regular}
    hash_args = ["hash-object", "-w", "--no-filters", "--"]
    for chunk in _git_path_batches(list(owners), args=hash_args, env=env):
        # Explicit argv operands need no stdin-paths C-style quoting, even
        # for embedded newlines, tabs, quotes or leading dashes.
        blobs = _run_git(
            [*hash_args, *chunk],
            cwd=str(root),
            env=env,
            check=True,
            binary=True,
        ).stdout.splitlines()
        if len(blobs) != len(chunk):
            raise CheckpointError(
                "Git returned an unexpected checkpoint hash count",
            )
        for source, blob in zip(chunk, blobs, strict=True):
            rel = owners[source]
            entries.append(
                os.fsencode(
                    f"{snapshot.modes[rel]} {blob.decode('ascii')}\t{rel}\0",
                ),
            )
    return entries


def _update_raw_index(
    *,
    root: Path,
    paths: list[str],
    env: dict[str, str],
    remove_nonfiles: bool = False,
    trust_filemode: bool = True,
) -> list[str]:
    """Batch raw blobs into a private index, returning supported target paths.

    Regular-file bytes are copied once, through no-follow descriptors, into a
    private staging directory and hashed from there, so Git never resolves a
    working-tree pathname itself.

    Args:
        root: Repository root.
        paths: Literal repo-relative target paths.
        env: Temporary-index environment overlay.
        remove_nonfiles: For diff, record a captured file replaced by a non-file
            (or now beyond a symlinked parent) as deleted. Capture instead
            excludes unsupported targets and refuses symlinked parents.
        trust_filemode: When False, keep the index's executable bits for
            regular files (Git's ``core.filemode=false`` semantics).

    Returns:
        Paths representing regular files, file symlinks, or absence.

    Raises:
        CheckpointError: When a filesystem read or Git operation fails.
    """
    try:
        staging = Path(tempfile.mkdtemp(prefix="lintro-git-blobs-"))
    except OSError as exc:
        raise CheckpointError("Failed to allocate checkpoint staging") from exc
    try:
        snapshot = _target_modes(
            root=root,
            paths=paths,
            remove_nonfiles=remove_nonfiles,
            staging=staging,
        )
        modes = snapshot.modes
        if not trust_filemode:
            _ignore_executable_bits(root=root, modes=modes, env=env)
        missing = [
            rel
            for rel, mode in modes.items()
            if mode == "0" or (mode is None and remove_nonfiles)
        ]
        remove_args = ["update-index", "--force-remove", "--"]
        for chunk in _git_path_batches(missing, args=remove_args, env=env):
            _run_git(
                [*remove_args, *chunk],
                cwd=str(root),
                env=env,
                check=True,
            )
        entries = _hash_regular_files(root=root, snapshot=snapshot, env=env)
        for rel, link in snapshot.links.items():
            blob = _run_git(
                ["hash-object", "-w", "--no-filters", "--stdin"],
                cwd=str(root),
                env=env,
                check=True,
                binary=True,
                input_bytes=link,
            ).stdout.strip()
            entries.append(b"120000 " + blob + b"\t" + os.fsencode(rel) + b"\0")
        if entries:
            _run_git(
                ["update-index", "--replace", "-z", "--index-info"],
                cwd=str(root),
                env=env,
                check=True,
                binary=True,
                input_bytes=b"".join(entries),
            )
    except OSError as exc:
        raise CheckpointError("Failed to read checkpoint targets") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return [rel for rel, mode in modes.items() if mode is not None]


def capture_checkpoint(
    paths: list[str] | tuple[str, ...],
    *,
    workspace_root: Path,
    run_id: str | None = None,
    keep: int = DEFAULT_CHECKPOINT_RETENTION,
) -> Checkpoint | None:
    """Snapshot target file state to ``refs/lintro/checkpoints/<run-id>``.

    Uses a temporary ``GIT_INDEX_FILE``: ``read-tree HEAD`` (or empty tree),
    raw ``hash-object`` / ``update-index`` for targets (including untracked),
    ``write-tree``, ``update-ref``. Blobs contain the actual working-tree bytes,
    bypassing newline conversion and clean filters without changing Git config.
    Like ``git add``, a target beyond a symlinked parent directory is refused,
    so no bytes are read through the link and no ref is written.
    Does not modify the user index, stash, or ``HEAD``. Directory enumeration
    failures propagate as :class:`CheckpointError`, aborting capture before
    any checkpoint refs are pruned or written.

    Temporary-index allocation, close, and cleanup failures propagate
    as :class:`OSError`; cleanup can fail after the new ref has been written.
    Git subprocess launch errors are wrapped as :class:`CheckpointError`:
    failures inside the later capture try block return None instead, while
    directory-listing failures propagate as described above. Best-effort retention
    pruning first attempts to reserve a slot by keeping at most
    ``max(keep - 1, 0)`` older refs. It is not rolled back on later failure:
    if temporary-index allocation fails, successfully pruned refs stay deleted
    and no new ref is created.

    Args:
        paths: Files (and/or directories) to include in the snapshot.
        workspace_root: Directory inside the repository.
        run_id: Optional run identifier; generated when omitted.
        keep: Total checkpoint refs to retain, this run's included (default
            10). Older refs are pruned *before* the new one is written, so
            the checkpoint a run depends on is never the one pruned — even at
            ``keep=0``, which keeps only the current run's ref.

    Returns:
        The created :class:`Checkpoint`, or None when git checkpoints are
        unavailable, no paths remain after normalization, a target lies beyond
        a symlinked parent directory, or a filesystem read or git plumbing step
        fails after the temporary index has been allocated.
    """
    if not git_checkpoints_available(workspace_root):
        return None
    root = _repo_root(workspace_root)
    if root is None:
        return None
    base = workspace_root.resolve()
    rel_paths = _normalize_paths(paths, root=root, base=base)
    if not rel_paths:
        return None

    rid = run_id or _new_run_id()
    ref = f"{CHECKPOINT_REF_PREFIX}{rid}"
    cwd = str(root)

    # Prune first: pruning after the write could delete the ref this very run
    # is about to hand back to the caller.
    try:
        prune_checkpoints(workspace_root=root, keep=max(keep - 1, 0))
    except CheckpointError as exc:
        logger.debug("Checkpoint prune skipped: {}", exc)

    fd, index_path = tempfile.mkstemp(prefix="lintro-git-index-")
    os.close(fd)
    index_file = Path(index_path)
    env = {"GIT_INDEX_FILE": str(index_file)}
    try:
        # Start from HEAD when available; otherwise empty tree (unborn branch).
        head = _run_git(["rev-parse", "--verify", "HEAD"], cwd=cwd)
        if head.returncode == 0:
            _run_git(["read-tree", "HEAD"], cwd=cwd, env=env, check=True)
        else:
            _run_git(["read-tree", "--empty"], cwd=cwd, env=env, check=True)

        # A target tracked in HEAD but already gone from the working tree
        # must be recorded as absent, or a rollback would resurrect it.
        # Anything that exists but is not a regular file (a directory, a
        # symlink to one, a FIFO) is dropped from the snapshot entirely: it
        # cannot be captured as a blob, and leaving it in ``paths`` would let
        # a restore delete a path lintro never touched.
        #
        # ``git add`` would apply contributor clean filters and CRLF conversion.
        # Insert raw blobs directly, touching only the temporary index.
        rel_paths = _update_raw_index(root=root, paths=rel_paths, env=env)

        tree = _run_git(["write-tree"], cwd=cwd, env=env, check=True)
        tree_sha = tree.stdout.strip()
        if not tree_sha:
            logger.debug("write-tree returned empty tree sha")
            return None

        _run_git(
            ["update-ref", ref, tree_sha],
            cwd=cwd,
            check=True,
        )
        logger.debug("Captured AI checkpoint {} ({})", ref, tree_sha)
    except CheckpointError as exc:
        logger.debug(
            "Checkpoint capture failed ({}); caller should use file fallback",
            exc,
        )
        return None
    finally:
        index_file.unlink(missing_ok=True)

    return Checkpoint(
        ref=ref,
        run_id=rid,
        root=root,
        paths=tuple(rel_paths),
        tree_sha=tree_sha,
        base=base,
    )


def _blob_for_path(
    *,
    root: Path,
    treeish: str,
    rel_path: str,
) -> tuple[bytes, str] | None:
    """Return blob bytes and the git mode for ``rel_path`` at ``treeish``.

    Args:
        root: Repository root.
        treeish: Tree object (or ref) to read from.
        rel_path: Repo-relative path inside that tree.

    Returns:
        ``(contents, git mode)``, or None when the path is absent from the
        tree or is a submodule pointer, which cannot be restored from a blob.
        A tree entry that git then refuses to read raises
        :class:`CheckpointError` from :func:`_run_git`.
    """
    entry = _run_git(
        ["ls-tree", "-z", "--", treeish, rel_path],
        cwd=str(root),
        check=True,
    )
    if not entry.stdout.strip():
        # Genuinely absent from the tree: the path was created after capture.
        return None
    git_mode = entry.stdout.split(" ", 1)[0]
    if git_mode in (_GITLINK_MODE, _TREE_MODE):
        # A submodule pointer has no blob, and ``cat-file -p`` on a tree
        # prints a directory listing that must never be written as content.
        logger.debug("Skipping non-blob entry in checkpoint: {}", rel_path)
        return None
    raw = _run_git(
        ["cat-file", "-p", f"{treeish}:{rel_path}"],
        cwd=str(root),
        check=True,
        binary=True,
    )
    return raw.stdout, git_mode


def _restore_by_path(abs_path: Path, blob: tuple[bytes, str] | None) -> None:
    """Restore one target by pathname (platforms without ``dir_fd`` support).

    Args:
        abs_path: Absolute target path.
        blob: ``(contents, git mode)``, or None to delete the target.
    """
    if blob is None:
        # ``missing_ok`` because a concurrent delete between the read and
        # write phases must not abandon the rest of the rollback.
        abs_path.unlink(missing_ok=True)
        return
    contents, git_mode = blob
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    if git_mode == _SYMLINK_MODE:
        # The blob of a symlink entry is its target path, not file content.
        abs_path.unlink(missing_ok=True)
        abs_path.symlink_to(contents.decode("utf-8", errors="replace"))
        return
    if abs_path.is_symlink():
        # Never write through a link: that would rewrite its target.
        abs_path.unlink()
    atomic_write_bytes(
        abs_path,
        contents,
        mode=0o755 if git_mode.endswith("755") else 0o644,
    )


def _write_at(parent: int, name: str, contents: bytes, mode: int) -> None:
    """Atomically replace ``name`` in a pinned directory with ``contents``.

    Args:
        parent: Pinned descriptor of the destination directory.
        name: Final path component.
        contents: Bytes to write.
        mode: Permission bits to force on the new file.

    Raises:
        BaseException: Re-raised after the partial temporary file is removed.
    """
    temp = f".{uuid.uuid4().hex}.lintro-tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(temp, flags, 0o600, dir_fd=parent)
    try:
        try:
            handle = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            handle.write(contents)
            os.fchmod(handle.fileno(), mode)
        # ``rename`` replaces a link at ``name`` itself, never its target.
        os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=parent)
        raise


def _restore_at(root_fd: int, rel: str, blob: tuple[bytes, str] | None) -> None:
    """Restore one target relative to a parent pinned without following links.

    Args:
        root_fd: Repository root descriptor; left open.
        rel: Repo-relative POSIX path.
        blob: ``(contents, git mode)``, or None to delete the target.

    Raises:
        CheckpointError: When a parent component is now a symbolic link.
    """
    try:
        parent = _open_parent(root_fd, rel, create=blob is not None)
    except _BeyondSymlinkError as exc:
        raise CheckpointError(
            f"Refusing to restore checkpoint target beyond a symbolic link: {rel}",
        ) from exc
    if parent is None:
        # Nothing to delete below an absent parent.
        return
    name = Path(rel).name
    try:
        if blob is None or blob[1] == _SYMLINK_MODE:
            # ``missing_ok`` semantics: a concurrent delete between the read
            # and write phases must not abandon the rest of the rollback.
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=parent)
        if blob is None:
            return
        contents, git_mode = blob
        if git_mode == _SYMLINK_MODE:
            # The blob of a symlink entry is its target path, not file content.
            text = contents.decode("utf-8", errors="replace")
            os.symlink(text, name, dir_fd=parent)
            return
        _write_at(parent, name, contents, 0o755 if git_mode.endswith("755") else 0o644)
    finally:
        os.close(parent)


def restore_checkpoint(
    checkpoint: Checkpoint,
    paths: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Restore files from a checkpoint tree.

    Every target blob is read before anything is written, so a tree that
    cannot be read fails the whole rollback without having touched the working
    tree. Each individual file is then replaced atomically and with the mode
    the checkpoint recorded, so a mode the run changed is rolled back too.

    The write phase is per-target and sequential, not one transaction: a
    failure part way through, including a parent directory found swapped for
    a symbolic link mid-restore, stops the rollback at that target and leaves
    the earlier targets already restored.

    Paths present on disk but absent from the checkpoint tree are removed
    (they were created after capture). Paths that remain at the checkpoint
    content are rewritten from the tree — including when the user edited
    them between capture and rollback (lintro targets always return to the
    pre-batch snapshot).

    Only paths recorded on the checkpoint are eligible. Anything else is
    ignored rather than deleted, so a caller passing an unrelated (or
    unresolvable) path can never destroy a file that was never snapshotted.

    Args:
        checkpoint: Checkpoint produced by :func:`capture_checkpoint`.
        paths: Optional subset of paths to restore; defaults to all paths
            recorded on the checkpoint. A failed write propagates to the
            caller after the partial temporary file is cleaned up.

    Raises:
        CheckpointError: Before any write, when a target's parent directory
            has since become a symbolic link; writing or deleting through it
            could reach outside the repository. Each write and deletion is
            also made relative to a parent directory pinned through no-follow
            descriptors, so a parent swapped for a link during the write phase
            stops the rollback there instead of being followed.
    """
    root = checkpoint.root
    treeish = checkpoint.tree_sha or checkpoint.ref
    # Fail before the read phase if the tree object itself is gone: every
    # subsequent lookup would report "absent", and absent means delete.
    _run_git(
        ["rev-parse", "--verify", "--quiet", f"{treeish}^{{tree}}"],
        cwd=str(root),
        check=True,
    )
    known = set(checkpoint.paths)
    if paths is None:
        target_rels = list(checkpoint.paths)
    else:
        requested = _normalize_paths(list(paths), root=root, base=checkpoint.base)
        target_rels = [rel for rel in requested if rel in known]
        skipped = sorted(set(requested) - known)
        if skipped:
            logger.debug(
                "Skipping restore for paths absent from checkpoint {}: {}",
                checkpoint.ref,
                skipped,
            )

    linked = [rel for rel in target_rels if _beyond_symlink(root, rel)]
    if linked:
        raise CheckpointError(
            f"Refusing to restore checkpoint targets beyond a symbolic link: {linked}",
        )
    planned: list[tuple[str, tuple[bytes, str] | None]] = []
    for rel in target_rels:
        planned.append(
            (rel, _blob_for_path(root=root, treeish=treeish, rel_path=rel)),
        )

    # Read phase complete — apply all writes/deletes.
    if not _DIR_FD_ACCESS:
        for rel, blob in planned:
            _restore_by_path(root / rel, blob)
        return
    root_fd = _open_root(root)
    try:
        for rel, blob in planned:
            _restore_at(root_fd, rel, blob)
    finally:
        os.close(root_fd)


def _trusts_filemode(root: Path) -> bool:
    """Return Git's effective ``core.filemode`` (true when unset or unreadable).

    Args:
        root: Repository root.

    Returns:
        False only when the repository configures ``core.filemode=false``.
    """
    result = _run_git(
        ["config", "--type=bool", "--get", "core.filemode"],
        cwd=str(root),
    )
    return result.returncode != 0 or result.stdout.strip() != "false"


def diff_checkpoint(
    checkpoint: Checkpoint,
    paths: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return a unified diff of working tree vs the checkpoint for ``paths``.

    This is the accurate post-run "what lintro changed" report for files that
    were included in the checkpoint.

    A throwaway index is loaded from the checkpoint, then updated with raw
    current target bytes. Comparing trees via ``--cached`` avoids clean filters
    and newline conversion on either side, including for untracked targets.
    As in ``git diff``, a target now beyond a symlinked parent directory shows
    as deleted (it is never read through the link), and with
    ``core.filemode=false`` executable-bit changes are not reported. Restore
    still writes the mode recorded at capture.

    Args:
        checkpoint: Checkpoint to diff against.
        paths: Optional path subset; defaults to checkpoint paths.

    Returns:
        Unified diff text (may be empty when nothing changed).
    """
    root = checkpoint.root
    known = set(checkpoint.paths)
    if paths is None:
        rels = list(checkpoint.paths)
    else:
        rels = [
            rel
            for rel in _normalize_paths(list(paths), root=root, base=checkpoint.base)
            if rel in known
        ]
    if not rels:
        return ""
    treeish = checkpoint.tree_sha or checkpoint.ref

    fd, index_path = tempfile.mkstemp(prefix="lintro-git-diff-index-")
    os.close(fd)
    index_file = Path(index_path)
    env = {"GIT_INDEX_FILE": str(index_file)}
    try:
        _run_git(["read-tree", treeish], cwd=str(root), env=env, check=True)
        _update_raw_index(
            root=root,
            paths=rels,
            env=env,
            remove_nonfiles=True,
            trust_filemode=_trusts_filemode(root),
        )
        # ``--no-color`` defends against a user's ``color.ui = always``.
        # ``--no-textconv`` matters as much as ``--no-ext-diff``: a repo's
        # ``diff.*.textconv`` would otherwise run an external program here.
        diff_args = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--cached",
            treeish,
            "--",
        ]
        chunks: list[bytes] = []
        for chunk in _git_path_batches(rels, args=diff_args, env=env):
            result = _run_git(
                [*diff_args, *chunk],
                cwd=str(root),
                env=env,
                check=True,
                binary=True,
            )
            chunks.append(result.stdout)
    finally:
        index_file.unlink(missing_ok=True)
    # Decoded leniently: a UTF-16 file with a ``diff`` attribute would make
    # strict locale decoding raise outside this module's error contract.
    diff_text: str = b"".join(chunks).decode("utf-8", errors="replace")
    return diff_text


def checkpoint_ref_exists(checkpoint: Checkpoint) -> bool:
    """Return whether a checkpoint's ref is still present.

    A later capture in the same run can prune an earlier ref under a tight
    retention setting, which would make a printed ``git diff <ref>`` hint fail
    even though the tree object itself is still reachable.

    Args:
        checkpoint: Checkpoint to probe.

    Returns:
        True when the ref still resolves.
    """
    try:
        result = _run_git(
            ["show-ref", "--verify", "--quiet", checkpoint.ref],
            cwd=str(checkpoint.root),
        )
    except CheckpointError:
        return False
    return result.returncode == 0


def list_checkpoint_refs(*, workspace_root: Path) -> list[str]:
    """List lintro checkpoint refs (oldest first).

    Args:
        workspace_root: Directory inside the repository.

    Returns:
        Full ref names sorted lexicographically (timestamp-prefixed run ids).
    """
    if not git_checkpoints_available(workspace_root):
        return []
    root = _repo_root(workspace_root)
    if root is None:
        return []
    result = _run_git(
        [
            "for-each-ref",
            "--format=%(refname)",
            "--sort=refname",
            CHECKPOINT_REF_PREFIX,
        ],
        cwd=str(root),
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def prune_checkpoints(
    *,
    workspace_root: Path,
    keep: int = DEFAULT_CHECKPOINT_RETENTION,
) -> int:
    """Delete oldest checkpoint refs beyond ``keep``.

    Args:
        workspace_root: Directory inside the repository.
        keep: Number of newest refs to retain. When ``keep <= 0``, all
            checkpoint refs are deleted.

    Returns:
        Number of refs deleted.
    """
    refs = list_checkpoint_refs(workspace_root=workspace_root)
    if keep < 0:
        keep = 0
    if len(refs) <= keep:
        return 0
    to_delete = refs if keep == 0 else refs[: len(refs) - keep]
    root = _repo_root(workspace_root)
    if root is None:
        return 0
    deleted = 0
    for ref in to_delete:
        # Tolerate a ref another lintro process already removed; pruning is
        # best-effort housekeeping and must never fail a fix run.
        result = _run_git(["update-ref", "-d", ref], cwd=str(root))
        if result.returncode == 0:
            deleted += 1
        else:
            logger.debug("Could not prune checkpoint ref {}", ref)
    return deleted


__all__ = [
    "CHECKPOINT_REF_PREFIX",
    "DEFAULT_CHECKPOINT_RETENTION",
    "Checkpoint",
    "CheckpointError",
    "capture_checkpoint",
    "checkpoint_ref_exists",
    "diff_checkpoint",
    "git_checkpoints_available",
    "list_checkpoint_refs",
    "prune_checkpoints",
    "restore_checkpoint",
]
