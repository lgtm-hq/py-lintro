"""AI modification audit log."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion

AUDIT_DIR = ".lintro-cache/ai"
AUDIT_JSONL_FILE = "audit.jsonl"
"""JSON Lines audit log — one run record appended per line."""

AUDIT_MAX_ENTRIES = 1000
"""Default cap on retained audit records; oldest lines are dropped past this."""

AUDIT_LOCK_FILE = "audit.jsonl.lock"
"""Sibling lock file used for cross-platform exclusive locking."""


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
    lock_path = audit_dir / AUDIT_LOCK_FILE

    line = json.dumps(audit, ensure_ascii=False)
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        _lock_file(lock_fh)
        try:
            with audit_path.open("a+", encoding="utf-8") as fh:
                fh.seek(0, 2)
                fh.write(line + "\n")
                fh.flush()
            _rotate_audit_log_atomic(audit_path, max_entries)
        finally:
            _unlock_file(lock_fh)


def _lock_file(fh: IO[str]) -> None:
    """Acquire an exclusive lock on an open file handle.

    Args:
        fh: Open text file handle to lock.
    """
    if sys.platform == "win32":
        import msvcrt

        # Lock the first byte of the sibling lock file.
        # msvcrt.LK_LOCK only retries ~10s before raising OSError, so loop
        # until the shared audit section can run (match POSIX blocking).
        fh.seek(0)
        if fh.read(1) == "":
            fh.write("0")
            fh.flush()
        while True:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.05)

    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)


def _unlock_file(fh: IO[str]) -> None:
    """Release a previously acquired exclusive file lock.

    Args:
        fh: Open text file handle to unlock.
    """
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _rotate_audit_log_atomic(
    audit_path: Path,
    max_entries: int | None,
) -> None:
    """Trim the JSONL audit log via temp file + atomic replace.

    Args:
        audit_path: Path to the ``audit.jsonl`` file.
        max_entries: Maximum records to retain; ``None`` or non-positive
            leaves the file untouched.

    Raises:
        Exception: Re-raises any failure after best-effort temp cleanup.
    """
    if max_entries is None or max_entries <= 0:
        return
    if not audit_path.is_file():
        return
    lines = [
        ln for ln in audit_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    if len(lines) <= max_entries:
        return
    kept = lines[-max_entries:]
    content = "\n".join(kept) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix="audit-",
        suffix=".jsonl",
        dir=str(audit_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
            tmp_fh.write(content)
            tmp_fh.flush()
            os.fsync(tmp_fh.fileno())
        os.replace(tmp_name, audit_path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise
