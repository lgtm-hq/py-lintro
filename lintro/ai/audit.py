"""AI modification audit log."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion

AUDIT_DIR = ".lintro-cache/ai"
AUDIT_FILE = "audit.json"
"""Legacy single-run audit filename (no longer written; kept for reference)."""

AUDIT_JSONL_FILE = "audit.jsonl"
"""JSON Lines audit log — one run record appended per line."""

AUDIT_MAX_ENTRIES = 1000
"""Default cap on retained audit records; oldest lines are dropped past this."""


def write_audit_log(
    workspace_root: Path,
    applied: list[AIFixSuggestion],
    rejected_count: int,
    total_cost: float,
    *,
    max_entries: int | None = AUDIT_MAX_ENTRIES,
) -> None:
    """Append one audit record for the AI fix run to the JSONL audit log.

    Records are appended (never overwritten) to ``audit.jsonl`` as one
    JSON object per line, preserving history across runs. When the file
    exceeds ``max_entries`` records, the oldest lines are dropped so the
    file stays bounded (simple size-based rotation).

    Append and rotation share an exclusive file lock so concurrent lintro
    processes cannot interleave a rewrite over another process's append.

    Args:
        workspace_root: Project root directory.
        applied: List of applied fix suggestions.
        rejected_count: Number of rejected suggestions.
        total_cost: Cumulative cost in USD.
        max_entries: Maximum retained records; ``None`` or non-positive
            disables rotation and keeps the full history.
    """
    entries = [
        {
            "file": s.file,
            "line": s.line,
            "code": s.code,
            "tool": s.tool_name,
            "action": "applied",
            "confidence": s.confidence,
            "risk_level": s.risk_level,
            "cost": s.cost_estimate,
        }
        for s in applied
    ]
    audit = {
        "timestamp": time.time(),
        "applied_count": len(applied),
        "rejected_count": rejected_count,
        "total_cost_usd": round(total_cost, 6),
        "entries": entries,
    }
    audit_dir = workspace_root / AUDIT_DIR
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / AUDIT_JSONL_FILE

    line = json.dumps(audit, ensure_ascii=False)
    with audit_path.open("a+", encoding="utf-8") as fh:
        _lock_file(fh)
        try:
            fh.seek(0, 2)
            fh.write(line + "\n")
            fh.flush()
            _rotate_audit_log_locked(fh, max_entries)
        finally:
            _unlock_file(fh)


def _lock_file(fh: IO[str]) -> None:
    """Acquire an exclusive lock on an open file handle when available.

    Args:
        fh: Open text file handle to lock.
    """
    if sys.platform == "win32":
        return
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)


def _unlock_file(fh: IO[str]) -> None:
    """Release a previously acquired exclusive file lock when available.

    Args:
        fh: Open text file handle to unlock.
    """
    if sys.platform == "win32":
        return
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _rotate_audit_log_locked(
    fh: TextIO,
    max_entries: int | None,
) -> None:
    """Trim the JSONL audit log while holding the caller's exclusive lock.

    Args:
        fh: Open audit file handle positioned after the latest append.
        max_entries: Maximum records to retain; ``None`` or non-positive
            leaves the file untouched.
    """
    if max_entries is None or max_entries <= 0:
        return
    fh.seek(0)
    lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    if len(lines) <= max_entries:
        return
    kept = lines[-max_entries:]
    fh.seek(0)
    fh.truncate()
    fh.write("\n".join(kept) + "\n")
    fh.flush()


def _rotate_audit_log(audit_path: Path, max_entries: int | None) -> None:
    """Trim the JSONL audit log to its most recent ``max_entries`` records.

    Args:
        audit_path: Path to the ``audit.jsonl`` file.
        max_entries: Maximum records to retain; ``None`` or non-positive
            leaves the file untouched.
    """
    if max_entries is None or max_entries <= 0:
        return
    with audit_path.open("a+", encoding="utf-8") as fh:
        _lock_file(fh)
        try:
            _rotate_audit_log_locked(fh, max_entries)
        finally:
            _unlock_file(fh)
