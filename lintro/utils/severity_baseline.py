"""Persist the previous run's severity counts so a delta can be reported.

The count delta that replaced the health score (issue #1739) needs something
to compare against. Lintro stores it next to the existing run artefacts, in
``.lintro/severity-baseline.json``, at the root of the log directory rather
than inside a ``run-*`` directory so run pruning never deletes it.

Scope is a fingerprint of the sorted, normalised path arguments and the set
of non-skipped tool names. The file maps each scope to its latest counts, so
scoped runs preserve the full-run entry and each scope advances independently.
Older single-scope files are migrated on write. Legacy baselines without
scope and unreadable files are incomparable and replaced by the next eligible
run, restoring deltas automatically on the following run.

Writers hold a persistent sibling file lock across reading, merging and
atomically replacing the baseline. Readers see either complete version.

Every operation here is best-effort: a workspace with no baseline, an
unreadable file, or a read-only directory must never fail a lint run. The
delta simply is not reported.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.models.core.severity_counts import SeverityCounts

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from lintro.models.core.tool_result import ToolResult

__all__ = [
    "SEVERITY_BASELINE_FILENAME",
    "read_severity_baseline",
    "resolve_log_root",
    "severity_baseline_scope",
    "write_severity_baseline",
]

SEVERITY_BASELINE_FILENAME: str = "severity-baseline.json"


def severity_baseline_scope(
    *,
    paths: Sequence[str],
    tool_results: Sequence[ToolResult],
) -> str:
    """Fingerprint the paths and tools measured by a run.

    Args:
        paths: Path arguments supplied to the run.
        tool_results: Every result, including skipped placeholders.

    Returns:
        str: Stable SHA-256 of sorted absolute paths and executed tool names.
    """
    scope = {
        "paths": sorted({str(Path(path).resolve()) for path in paths}),
        "tools": sorted({result.name for result in tool_results if not result.skipped}),
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


def read_severity_baseline(base_dir: Path | str, scope: str) -> SeverityCounts | None:
    """Read the previous run's counts only for a matching scope.

    Args:
        base_dir: Root of the run-log directory (normally ``.lintro``).
        scope: Fingerprint of the current run's paths and executed tools.

    Returns:
        SeverityCounts | None: Recorded counts, or ``None`` for a missing,
        unreadable, legacy, or differently scoped baseline.
    """
    scopes = _read_baseline(_baseline_path(base_dir))
    if scopes is None:
        return None
    return _parse_counts(scopes.get(scope))


@contextmanager
def _baseline_lock(path: Path) -> Iterator[None]:
    """Lock a stable sibling file until the handle closes, including on error.

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
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


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
    scope: str,
) -> None:
    """Record the current run's severity counts for the next run to compare.

    Failures are logged at debug level and swallowed: an unwritable log
    directory costs the next run its delta line, nothing more.

    Args:
        base_dir: Root of the run-log directory (normally ``.lintro``).
        counts: Severity tallies for the run that just finished.
        scope: Fingerprint of the current run's paths and executed tools.
    """
    path = _baseline_path(base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _baseline_lock(path):
            scopes = _read_baseline(path) or {}
            scopes[scope] = counts.to_dict()
            _replace_baseline(path, scopes)
    except OSError as exc:
        logger.debug(f"Failed to write severity baseline {path}: {exc}")
