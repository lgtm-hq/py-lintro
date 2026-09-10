"""Persist the previous run's severity counts so a delta can be reported.

The count delta that replaced the health score (issue #1739) needs something
to compare against. Lintro stores it next to the existing run artefacts, in
``.lintro/severity-baseline.json``, at the root of the log directory rather
than inside a ``run-*`` directory so run pruning never deletes it.

Every operation here is best-effort: a workspace with no baseline, an
unreadable file, or a read-only directory must never fail a lint run. The
delta simply is not reported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.models.core.severity_counts import SeverityCounts

__all__ = [
    "SEVERITY_BASELINE_FILENAME",
    "read_severity_baseline",
    "resolve_log_root",
    "write_severity_baseline",
]

SEVERITY_BASELINE_FILENAME: str = "severity-baseline.json"


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


def read_severity_baseline(base_dir: Path | str) -> SeverityCounts | None:
    """Read the previous run's severity counts, if any were recorded.

    Args:
        base_dir: Root of the run-log directory (normally ``.lintro``).

    Returns:
        SeverityCounts | None: The recorded counts, or ``None`` when no
        readable baseline exists.
    """
    path = _baseline_path(base_dir)
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
    return SeverityCounts.from_dict(data)


def write_severity_baseline(base_dir: Path | str, counts: SeverityCounts) -> None:
    """Record the current run's severity counts for the next run to compare.

    Failures are logged at debug level and swallowed: an unwritable log
    directory costs the next run its delta line, nothing more.

    Args:
        base_dir: Root of the run-log directory (normally ``.lintro``).
        counts: Severity tallies for the run that just finished.
    """
    path = _baseline_path(base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(counts.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug(f"Failed to write severity baseline {path}: {exc}")
