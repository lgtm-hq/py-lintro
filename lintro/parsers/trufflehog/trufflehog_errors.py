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

# zap log levels that signal a failure. TruffleHog logs progress as
# ``info-0``/``debug-N`` and failures as ``error``; ``dpanic``/``panic``/
# ``fatal`` are zap's escalating fatal levels.
_ERROR_LEVELS: frozenset[str] = frozenset(
    {"error", "dpanic", "panic", "fatal"},
)


def extract_trufflehog_scan_errors(stderr: str) -> list[str]:
    """Extract per-path scan error strings from TruffleHog stderr.

    JSON log records are classified by severity, not by message:

    * The aggregate ``encountered errors during scan`` payload takes
      precedence — its ``errors`` array is expanded so each reason stays
      individually classifiable against the resolved scan set.
    * Any other error-severity record (``level`` of ``error``/``fatal``/
      ``panic``, or a non-empty ``error``/``err`` field) is retained.
    * Routine progress records (``running source``, ``finished scanning``, …)
      carry an informational level and no error field, so they are ignored.

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

    def _record(reason: str) -> None:
        """Append a reason once, preserving first-seen order.

        Args:
            reason: The error reason string to retain.
        """
        if reason and reason not in seen:
            seen.add(reason)
            extracted.append(reason)

    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        payload = _json_log_payload(stripped)
        if payload is not None:
            aggregate = _errors_from_payload(payload)
            if aggregate:
                for err in aggregate:
                    _record(err)
            elif _is_error_record(payload):
                # An error-severity record that is not the aggregate payload
                # (or an aggregate with no usable reasons). Keep it so the
                # caller cannot mistake the batch for a clean scan.
                _record(_reason_from_error_record(payload, raw=stripped))
            continue

        # Plain-text / logfmt line. Retain it whatever it says — an
        # unclassifiable diagnostic must not be silently dropped.
        _record(stripped)

    return extracted


def stderr_reports_scan_errors(stderr: str) -> bool:
    """Return whether TruffleHog stderr reports any scan error.

    TruffleHog exits 0 either way, so this is the gate that decides whether a
    batch needs error classification at all. It fires on the aggregate
    ``encountered errors during scan`` banner *and* on any standalone
    error-severity JSON record: when an unreadable file is reached through a
    scanned directory, TruffleHog logs only the standalone record and no
    aggregate, which would otherwise read as a clean scan (#1662).

    Plain-text stderr is matched on the banner alone, so ordinary non-JSON
    log noise does not trip the gate.

    Args:
        stderr: Raw stderr captured from a TruffleHog run.

    Returns:
        True when the stderr carries at least one scan-error signal.
    """
    if not stderr or not stderr.strip():
        return False

    if "encountered errors during scan" in stderr:
        return True

    return any(
        (payload := _json_log_payload(line.strip())) is not None
        and _is_error_record(payload)
        for line in stderr.splitlines()
        if line.strip()
    )


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


def _is_error_record(payload: dict[str, object]) -> bool:
    """Return whether a JSON log record reports an error.

    Classification is by severity rather than by message text, so a scan
    failure logged under any ``msg`` is caught. TruffleHog's zap logger emits
    ``level`` values such as ``info-0`` for progress and ``error`` for
    failures, and attaches the underlying reason as an ``error`` field.

    Args:
        payload: A decoded JSON log record from TruffleHog stderr.

    Returns:
        True when the record's level is error-like or it carries a non-empty
        ``error``/``err`` field.
    """
    level = payload.get("level")
    if isinstance(level, str):
        # ``info-0``/``debug-3`` carry a verbosity suffix; compare the stem.
        stem = level.strip().lower().split("-", 1)[0]
        if stem in _ERROR_LEVELS:
            return True

    return _error_field_text(payload) is not None


def _error_field_text(payload: dict[str, object]) -> str | None:
    """Return the record's error-reason text, if it carries one.

    Args:
        payload: A decoded JSON log record from TruffleHog stderr.

    Returns:
        The stripped ``error``/``err`` text, or None when neither field holds
        a non-empty string.
    """
    for key in ("error", "err"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _reason_from_error_record(payload: dict[str, object], *, raw: str) -> str:
    """Build a retained reason string for a non-aggregate error record.

    Args:
        payload: A decoded JSON error record from TruffleHog stderr.
        raw: The original stripped stderr line, used when the record carries
            no usable reason text.

    Returns:
        The record's ``error``/``err`` text when present, otherwise the raw
        line so no diagnostic detail is lost.
    """
    return _error_field_text(payload) or raw


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
