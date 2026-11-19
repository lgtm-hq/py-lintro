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


def delete_aside(path: Path) -> None:
    """Delete a path set aside, which nothing will run again.

    A link is removed itself, never followed. Parts another repair removes
    first are not failures. A leftover that stays is logged, not raised: it
    has no npm entry name, so it cannot change what runs, and the next repair
    tries again.

    Args:
        path: Entry already set aside, or a moved-aside name.
    """
    failures: list[str] = []

    def record(_func: object, failed: str, exc: BaseException) -> None:
        if not isinstance(exc, FileNotFoundError):
            failures.append(f"{failed}: {exc}")

    if path.is_dir() and not path.is_symlink():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=record)
        else:  # pragma: no cover - exercised on the 3.11 CI leg
            shutil.rmtree(path, onerror=lambda f, p, info: record(f, p, info[1]))
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    if failures and (path.exists() or path.is_symlink()):
        logger.warning(
            f"lintro could not delete the npm cache leftover {path} "
            f"({failures[0]}); it is not run and will be retried",
        )


def set_aside(entry: Path) -> bool:
    """Rename an entry aside atomically, then delete it.

    Args:
        entry: ``_npx`` entry to remove; the caller holds what keeps other
            lintro runs from using it.

    Returns:
        True when this call moved the entry aside; False when it was already
        gone.

    Raises:
        OSError: The entry is still in place and could not be moved aside.
    """
    aside = entry.with_name(aside_name(entry.name))
    try:
        os.replace(entry, aside)
    except OSError:
        # A missing source means another repair moved or removed it.
        if not (entry.exists() or entry.is_symlink()):
            return False
        raise
    delete_aside(aside)
    return True
