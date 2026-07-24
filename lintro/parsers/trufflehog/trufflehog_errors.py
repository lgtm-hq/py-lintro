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

# zap log levels that are routine advisories, never scan incompleteness. A
# JSON record at one of these levels *with no error field* is progress noise
# and is dropped; anything else — an error level, an ``error``/``err`` field,
# or an unrecognised/absent level — is retained so the caller fails closed
# (unknown means unsafe). ``warn`` is treated as advisory pending evidence
# that TruffleHog uses it for incompleteness (#1685).
_BENIGN_LEVELS: frozenset[str] = frozenset(
    {"info", "debug", "warn"},
)


def extract_trufflehog_scan_errors(stderr: str) -> list[str]:
    """Extract per-path scan error strings from TruffleHog stderr.

    The classification is deliberately asymmetric toward retention — unknown
    means unsafe:

    * The aggregate ``encountered errors during scan`` payload takes
      precedence — its ``errors`` array is expanded so each reason stays
      individually classifiable against the resolved scan set.
    * A JSON record positively identified as a routine advisory (an
      ``info``/``debug``/``warn`` level with no ``error``/``err`` field —
      ``running source``, ``finished scanning``, …) is dropped as progress
      noise.
    * Every other JSON record is retained: error/fatal/panic levels, records
      carrying an ``error``/``err`` field, and records whose structure we do
      not recognise at all (no known level). A structurally unknown record is
      never proven benign, so it is kept.
    * Every non-JSON line is retained verbatim.

    A retained line is never a benign missing path unless it is exactly an
    ``lstat``/``stat`` "no such file or directory" reason
    (:func:`is_benign_missing_path_error` returns False for everything else),
    so keeping it makes :func:`scan_errors_are_all_benign` return False and the
    caller fail closed on a possibly incomplete scan (#1044, #1662).

    Args:
        stderr: Raw stderr captured from a TruffleHog run.

    Returns:
        Ordered list of individual error reason strings. Empty when only
        routine progress records were present (a clean scan).
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
            elif not _is_benign_progress_record(payload):
                # Not the aggregate and not a positively-benign advisory: an
                # error record, a record with an error field, or a structure
                # we do not recognise. Keep it so the caller cannot mistake
                # the batch for a clean scan.
                _record(_reason_from_error_record(payload, raw=stripped))
            continue

        # Plain-text / logfmt line. Retain it whatever it says — an
        # unclassifiable diagnostic must not be silently dropped.
        _record(stripped)

    return extracted


def stderr_reports_scan_errors(stderr: str) -> bool:
    """Return whether TruffleHog stderr reports any scan error.

    TruffleHog exits 0 either way, so this is the gate that decides whether a
    batch needs error classification at all. It is defined as exactly "did
    extraction retain anything", so the gate and
    :func:`extract_trufflehog_scan_errors` can never diverge: any line the
    extractor keeps trips the gate, and a stderr of only routine progress
    records does not. This covers the aggregate ``encountered errors during
    scan`` banner, standalone error records emitted without an aggregate (an
    unreadable file reached through a scanned directory — #1662), and any
    unclassifiable JSON or plain-text diagnostic.

    Args:
        stderr: Raw stderr captured from a TruffleHog run.

    Returns:
        True when the stderr carries at least one line the extractor retains.
    """
    return bool(extract_trufflehog_scan_errors(stderr))


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


def _is_benign_progress_record(payload: dict[str, object]) -> bool:
    """Return whether a JSON log record is a routine advisory, not an error.

    Classification is by severity, not message text, so a scan failure logged
    under any ``msg`` is still caught by retaining everything this rejects.
    TruffleHog's zap logger emits ``level`` values such as ``info-0`` for
    progress and ``error`` for failures, and attaches the underlying reason as
    an ``error`` field.

    A record is benign progress only when it is positively recognised as such:
    an ``info``/``debug``/``warn`` level *and* no ``error``/``err`` field. A
    record with an error field, an error/fatal/panic level, or an
    unrecognised/absent level is not benign — the caller retains it so an
    unknown record fails the scan closed.

    Args:
        payload: A decoded JSON log record from TruffleHog stderr.

    Returns:
        True only when the record is a recognised advisory carrying no error.
    """
    if _error_field_text(payload) is not None:
        return False

    level = payload.get("level")
    if not isinstance(level, str):
        # No recognisable level — cannot prove it benign, so it is not.
        return False

    # ``info-0``/``debug-3`` carry a verbosity suffix; compare the stem.
    stem = level.strip().lower().split("-", 1)[0]
    return stem in _BENIGN_LEVELS


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
