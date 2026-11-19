"""Set npm cache entries aside and delete them (#3406, #3530).

An entry lintro removes is first renamed to a ``.lintro-stale-`` name, which
npx never runs from and :func:`~lintro.utils.npx_integrity.cache_entries`
skips, and only then deleted. Both the repair gate
(:mod:`lintro.tools.core.npx_cache`) and the spawn-time check, which discards
a cold install npx did not finish (:mod:`lintro.utils.npx_call`), use these.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import uuid
from pathlib import Path

from loguru import logger

from lintro.utils.npx_integrity import STALE_PREFIX

#: Characters of the original name kept in an aside name, for debugging.
ASIDE_BASE_CHARS: int = 32


def aside_name(name: str) -> str:
    """Build a unique name, of bounded length, for an entry set aside.

    Args:
        name: The entry's current name.

    Returns:
        ``.lintro-stale-<base>-<uuid>``, where *base* is at most
        :data:`ASIDE_BASE_CHARS` characters of the original name with any
        stale prefixes removed, so the result never grows with *name*.
    """
    base = name
    while base.startswith(STALE_PREFIX):
        base = base[len(STALE_PREFIX) :]
    return f"{STALE_PREFIX}{base[:ASIDE_BASE_CHARS]}-{uuid.uuid4().hex}"


def _lexists(path: Path, dir_fd: int | None) -> bool:
    """Report whether *path* names anything, a dangling link included.

    Args:
        path: The name, relative to *dir_fd* when one is given.
        dir_fd: Directory descriptor *path* is relative to, or None.

    Returns:
        True when something is there, or when that cannot be told.
    """
    try:
        os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def delete_aside(path: Path, *, dir_fd: int | None = None) -> None:
    """Delete a path set aside, which nothing will run again.

    A link is removed itself, never followed. Parts another repair removes
    first are not failures. A leftover that stays is logged, not raised: it
    has no npm entry name, so it cannot change what runs, and the next repair
    tries again. With *dir_fd*, every step is taken relative to that open
    directory, so a directory above it that is renamed or replaced by a link
    meanwhile cannot redirect the removal (#3530).

    Args:
        path: Entry already set aside, or a moved-aside name; a bare name
            when *dir_fd* is given.
        dir_fd: Open directory that holds *path*, or None.
    """
    failures: list[str] = []

    def record(_func: object, failed: str, exc: BaseException) -> None:
        if not isinstance(exc, FileNotFoundError):
            failures.append(f"{failed}: {exc}")

    try:
        mode = os.stat(path, dir_fd=dir_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        failures.append(f"{path}: {exc}")
        mode = 0
    if stat.S_ISDIR(mode):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=record, dir_fd=dir_fd)
        else:  # pragma: no cover - exercised on the 3.11 CI leg
            shutil.rmtree(
                path,
                onerror=lambda f, p, info: record(f, p, info[1]),
                dir_fd=dir_fd,
            )
    elif not failures:
        try:
            os.unlink(path, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    if failures and _lexists(path, dir_fd):
        logger.warning(
            f"lintro could not delete the npm cache leftover {path} "
            f"({failures[0]}); it is not run and will be retried",
        )


def set_aside(entry: Path, *, dir_fd: int | None = None) -> bool:
    """Rename an entry aside atomically, then delete it.

    Args:
        entry: ``_npx`` entry to remove; the caller holds what keeps other
            lintro runs from using it. A bare name when *dir_fd* is given.
        dir_fd: Open ``_npx`` directory that holds *entry*, or None. With
            it, the rename and the deletion stay inside that directory.

    Returns:
        True when this call moved the entry aside; False when it was already
        gone.

    Raises:
        OSError: The entry is still in place and could not be moved aside.
    """
    aside = entry.with_name(aside_name(entry.name))
    try:
        os.replace(entry, aside, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError:
        # A missing source means another repair moved or removed it.
        if not _lexists(entry, dir_fd):
            return False
        raise
    delete_aside(aside, dir_fd=dir_fd)
    return True
