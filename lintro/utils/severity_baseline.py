"""Persist the previous run's severity counts so a delta can be reported.

The count delta that replaced the health score (issue #1739) needs something
to compare against. Lintro stores it next to the existing run artefacts, in
``.lintro/severity-baseline.json``, at the root of the log directory rather
than inside a ``run-*`` directory so run pruning never deletes it.

Scope is a fingerprint of the sorted, normalised path arguments and the set
of non-skipped tool names. The file maps each scope to its latest counts, so
scoped runs preserve the full-run entry and each scope advances independently.
At most 32 scopes are retained in last-write order. The latest full-path run
(paths resolving to the working directory) is protected from eviction; other
old scopes are discarded first. Reads do not refresh retention order.
``--diff`` and ``--incremental`` runs neither compare nor record baselines:
their changing file subsets are not represented by this scope fingerprint.
Older single-scope files are migrated on write. Legacy baselines without
scope and unreadable files are incomparable and replaced by the next eligible
run, restoring deltas automatically on the following run.

Writers hold a persistent sibling file lock across reading, merging and
atomically replacing the baseline. Readers see either complete version.
Lock contention costs at most one second before the optional update is skipped.

Every operation here is best-effort: a workspace with no baseline, an
unreadable file, or a read-only directory must never fail a lint run. The
delta simply is not reported.
"""

from __future__ import annotations

import errno
import hashlib
import json
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.models.core.severity_counts import SeverityCounts

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from lintro.models.core.tool_result import ToolResult

__all__ = [
    "MAX_SEVERITY_BASELINE_SCOPES",
    "SEVERITY_BASELINE_FILENAME",
    "read_severity_baseline",
    "resolve_log_root",
    "severity_baseline_scope",
    "write_severity_baseline",
]

SEVERITY_BASELINE_FILENAME: str = "severity-baseline.json"
MAX_SEVERITY_BASELINE_SCOPES: int = 32
_LOCK_TIMEOUT_SECONDS: float = 1.0


def severity_baseline_scope(
    *,
    paths: Sequence[str],
    tool_results: Sequence[ToolResult],
) -> str | None:
    """Fingerprint the paths and tools measured by a run.

    Args:
        paths: Path arguments supplied to the run.
        tool_results: Every result, including skipped placeholders.

    Returns:
        str | None: Stable SHA-256 of sorted absolute paths and executed tool
        names, or ``None`` when a path cannot be resolved.
    """
    try:
        resolved_paths = sorted({str(Path(path).resolve()) for path in paths})
    except (OSError, ValueError, RuntimeError):
        return None
    scope = {
        "paths": resolved_paths,
        "tools": sorted(
            {
                result.name
                for result in tool_results
                if not result.skipped and not result.unavailable
            },
        ),
    }
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode("utf-8")).hexdigest()


def resolve_log_root(output_manager: object) -> Path | None:
    """Return the log-directory root an output manager owns, if it has one.

    Guards the baseline against output-manager doubles and partially built
    managers: anything whose ``base_dir`` is not a real path yields ``None``,
    which simply skips the baseline rather than raising mid-run.

    Args:
        output_manager: The run's output manager, or any stand-in for it.

    Returns:
        Path | None: The log root, or ``None`` when there is not a usable one.
    """
    base_dir = getattr(output_manager, "base_dir", None)
    if isinstance(base_dir, Path):
        return base_dir
    if isinstance(base_dir, str):
        return Path(base_dir)
    return None


def _baseline_path(base_dir: Path | str) -> Path:
    """Return the baseline file path inside a log directory.

    Args:
        base_dir: Root of the run-log directory (normally ``.lintro``).

    Returns:
        Path: Location of the baseline file.
    """
    return Path(base_dir) / SEVERITY_BASELINE_FILENAME


def _read_baseline(path: Path) -> dict[str, Any] | None:
    """Read scope entries, including the older single-scope format.

    Args:
        path: Location of the baseline file.

    Returns:
        dict[str, Any] | None: Scope entries, or ``None`` if unreadable.
        Legacy counts without scope yield an empty mapping.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug(f"Ignoring unparseable severity baseline at {path}.")
        return None
    if not isinstance(data, dict):
        return None
    scopes = data.get("scopes")
    if isinstance(scopes, dict):
        return scopes
    scope = data.get("scope")
    return {scope: data} if isinstance(scope, str) else {}


def _parse_counts(data: object) -> SeverityCounts | None:
    """Reject damaged count payloads rather than interpreting them as clean.

    Args:
        data: One scope's serialized severity counts.

    Returns:
        SeverityCounts | None: Valid counts, or ``None`` for an invalid entry.
    """
    if not isinstance(data, dict):
        return None
    values = [data.get(key) for key in ("error", "warning", "info", "total")]
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in values
    ):
        return None
    counts = SeverityCounts.from_dict(data)
    return counts if data["total"] == counts.total else None


def read_severity_baseline(
    base_dir: Path | str,
    scope: str | None,
) -> SeverityCounts | None:
    """Read the previous run's counts only for a matching scope.

    Args:
        base_dir: Root of the run-log directory (normally ``.lintro``).
        scope: Fingerprint of the current run, or ``None`` if incomparable.

    Returns:
        SeverityCounts | None: Recorded counts, or ``None`` for a missing,
        unreadable, legacy, or differently scoped baseline.
    """
    if scope is None:
        return None
    scopes = _read_baseline(_baseline_path(base_dir))
    if scopes is None:
        return None
    return _parse_counts(scopes.get(scope))


def _acquire_baseline_lock(acquire: Callable[[], None]) -> None:
    """Retry a nonblocking lock briefly, then leave persistence to another run.

    Args:
        acquire: Platform-specific nonblocking exclusive lock operation.

    Raises:
        OSError: Acquisition failed or contention outlasted the deadline.
    """
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            acquire()
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


@contextmanager
def _baseline_lock(path: Path) -> Iterator[None]:
    """Lock a stable sibling file and explicitly unlock it, including on error.

    Keep the lock file in place: unlinking it would let waiters and new
    writers lock different inodes after an atomic baseline replacement.

    Args:
        path: Baseline path whose parent already exists.

    Yields:
        None: The caller owns the exclusive writer lock.
    """
    with path.with_name(path.name + ".lock").open("a+", encoding="utf-8") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            if handle.read(1) == "":
                handle.write("0")
                handle.flush()
            handle.seek(0)
            _acquire_baseline_lock(
                lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1),
            )
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            _acquire_baseline_lock(
                lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB),
            )
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _retained_scopes(
    scopes: dict[str, Any],
    scope: str,
    counts: SeverityCounts,
    *,
    full_run: bool,
) -> dict[str, Any]:
    """Validate entries and cap last-write order, protecting the latest full run.

    Args:
        scopes: Previously stored entries in their persisted write order.
        scope: Fingerprint to advance to the newest position.
        counts: Current run's counts.
        full_run: Whether this run measured the full working-directory path.

    Returns:
        dict[str, Any]: Valid, bounded entries in oldest-to-newest write order.
    """
    retained: dict[str, Any] = {}
    for key, entry in scopes.items():
        parsed = _parse_counts(entry)
        if parsed is not None:
            retained[key] = parsed.to_dict()
            if entry.get("full_run") is True:
                retained[key]["full_run"] = True
    retained.pop(scope, None)
    retained[scope] = counts.to_dict()
    if full_run:
        retained[scope]["full_run"] = True
    latest_full = next(
        (key for key in reversed(retained) if retained[key].get("full_run") is True),
        None,
    )
    for key in tuple(retained):
        if len(retained) <= MAX_SEVERITY_BASELINE_SCOPES:
            break
        if key != latest_full:
            del retained[key]
    return retained


def _replace_baseline(path: Path, scopes: dict[str, Any]) -> None:
    """Publish complete JSON, cleaning up the temporary file even on failure.

    Args:
        path: Baseline path whose parent already exists.
        scopes: Merged scope entries to persist.
    """
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump({"scopes": scopes}, handle, indent=2)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def write_severity_baseline(
    base_dir: Path | str,
    counts: SeverityCounts,
    scope: str | None,
    *,
    full_run: bool = False,
) -> None:
    """Record the current run's severity counts for the next run to compare.

    Failures are logged at debug level and swallowed: an unwritable log
    directory costs the next run its delta line, nothing more.

    Args:
        base_dir: Root of the run-log directory (normally ``.lintro``).
        counts: Severity tallies for the run that just finished.
        scope: Fingerprint of the current run, or ``None`` if incomparable.
        full_run: Whether the paths resolve to the full working directory.
    """
    if scope is None:
        return
    path = _baseline_path(base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _baseline_lock(path):
            scopes = _retained_scopes(
                _read_baseline(path) or {},
                scope,
                counts,
                full_run=full_run,
            )
            _replace_baseline(path, scopes)
    except OSError as exc:
        logger.debug(f"Failed to write severity baseline {path}: {exc}")
