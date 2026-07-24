"""Classification helpers for TruffleHog scan-diagnostic stderr.

TruffleHog exits 0 even when it cannot read a scan target, logging
``encountered errors during scan`` with per-path reasons. A security scanner
must fail closed on genuine incomplete scans (#1044), but CI-only artifact
paths that are absent locally (``coverage/``, ``lighthouse-reports/``, …) are
benign when they were never part of the resolved scan set (#1631).

Classification is deliberately conservative: only recognised shapes are ever
called benign, and anything that cannot be classified is kept so the caller
fails closed rather than reporting a clean pass (#1662).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Go's os.Lstat/Stat error form: ``lstat /path: no such file or directory``.
_MISSING_PATH_RE: re.Pattern[str] = re.compile(
    r"^(?:lstat|stat)\s+(?P<path>.+):\s+no such file or directory\s*$",
    re.IGNORECASE,
)


def extract_trufflehog_scan_errors(stderr: str) -> list[str]:
    """Extract per-path scan error strings from TruffleHog stderr.

    Structured JSON log lines are authoritative: the ``errors`` array of a
    scan-error payload carries every reason, and other JSON log records
    (``running source``, ``finished scanning``, …) are routine progress noise
    that is deliberately ignored.

    Every remaining non-empty line is retained verbatim, even when it matches
    no known error shape. This is deliberate: unknown means unsafe. An
    unclassified line is never a benign missing path
    (:func:`is_benign_missing_path_error` returns False for anything that is
    not an ``lstat``/``stat`` "no such file or directory" reason), so keeping
    it makes :func:`scan_errors_are_all_benign` return False and the caller
    fail closed on a possibly incomplete scan (#1044, #1662).

    Args:
        stderr: Raw stderr captured from a TruffleHog run.

    Returns:
        Ordered list of individual error reason strings. Empty when none can
        be extracted (caller should fail closed — the scan may be incomplete).
    """
    if not stderr or not stderr.strip():
        return []

    extracted: list[str] = []
    seen: set[str] = set()

    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        payload = _json_log_payload(stripped)
        if payload is not None:
            for err in _errors_from_payload(payload):
                if err not in seen:
                    seen.add(err)
                    extracted.append(err)
            continue

        # Plain-text / logfmt line. Retain it whatever it says — an
        # unclassifiable diagnostic must not be silently dropped.
        if stripped not in seen:
            seen.add(stripped)
            extracted.append(stripped)

    return extracted


def is_benign_missing_path_error(
    error: str,
    *,
    scan_paths: set[str] | frozenset[str],
) -> bool:
    """Return whether a scan error is a benign missing path outside the scan set.

    Args:
        error: A single TruffleHog error reason string.
        scan_paths: Absolute paths that were actually passed to TruffleHog for
            this batch (the resolved, existing scan set).

    Returns:
        True when the error is ``lstat``/``stat`` ``no such file or directory``
        for a path that is not among (and not a parent of) the resolved scan
        paths. Permission errors and missing scan-set targets are not benign.
    """
    match = _MISSING_PATH_RE.match(error.strip())
    if match is None:
        return False

    missing_raw = match.group("path").strip()
    if not missing_raw:
        return False

    try:
        missing = str(Path(missing_raw).resolve())
    except (OSError, RuntimeError, ValueError):
        # Unresolvable paths cannot be proven outside the scan set — fail closed.
        return False

    if missing in scan_paths:
        return False

    # A missing directory that contains scanned files would be a genuine
    # incomplete scan of those targets — treat as non-benign.
    missing_prefix = missing.rstrip("/") + "/"
    for scan_path in scan_paths:
        if scan_path == missing or scan_path.startswith(missing_prefix):
            return False

    return True


def scan_errors_are_all_benign(
    errors: list[str],
    *,
    scan_paths: set[str] | frozenset[str],
) -> bool:
    """Return whether every extracted scan error is a benign missing path.

    Args:
        errors: Per-path error strings from :func:`extract_trufflehog_scan_errors`.
        scan_paths: Absolute paths passed to TruffleHog for this batch.

    Returns:
        True only when ``errors`` is non-empty and every entry is benign.
        An empty list means reasons could not be parsed — fail closed.
    """
    if not errors:
        return False
    return all(
        is_benign_missing_path_error(err, scan_paths=scan_paths) for err in errors
    )


def _json_log_payload(line: str) -> dict[str, object] | None:
    """Decode a stderr line as a structured JSON log record.

    Args:
        line: A single stripped stderr line.

    Returns:
        The decoded mapping when the line is a JSON object, otherwise None
        (the caller then treats the line as unstructured text).
    """
    if not line.startswith("{"):
        return None

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None

    return payload if isinstance(payload, dict) else None


def _errors_from_payload(payload: dict[str, object]) -> list[str]:
    """Pull the ``errors`` array from a TruffleHog JSON log record.

    Args:
        payload: A decoded JSON log record from TruffleHog stderr.

    Returns:
        The string entries from ``errors``, or an empty list when the record
        is not a scan-error payload.
    """
    msg = payload.get("msg")
    if not isinstance(msg, str) or "encountered errors during scan" not in msg:
        return []

    raw_errors = payload.get("errors")
    if not isinstance(raw_errors, list):
        return []

    return [err for err in raw_errors if isinstance(err, str) and err.strip()]
