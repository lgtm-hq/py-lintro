"""Parser for Spectral JSON output.

Handles the JSON document emitted by ``spectral lint --format json``. Each
finding is an object with ``code``, ``path``, ``message``, ``severity``, and a
``range`` describing the source position. Spectral reports zero-based line and
character offsets, which are converted here to lintro's one-based convention.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.spectral.spectral_issue import SpectralIssue

# Spectral encodes severity as an integer diagnostic level. Map each level to
# lintro's severity vocabulary; ``hint`` normalizes to INFO downstream.
_SEVERITY_BY_LEVEL: dict[int, str] = {
    0: "error",
    1: "warning",
    2: "info",
    3: "hint",
}
_SPECTRAL_IDENTITY_KEYS: frozenset[str] = frozenset({"code", "message"})


def _looks_like_spectral_payload(data: Any) -> bool:
    """Return True when ``data`` matches Spectral's findings array.

    Node/npx/bunx and Spectral itself can emit a valid JSON array that is not
    the findings payload (``["warning"]``, a bracketed log line). Accept only
    a list containing an object with both a finding identity (``code`` or
    ``message``) and Spectral-shaped location data (a list-valued ``path`` or
    object-valued ``range``). Generic log objects often have ``message``,
    ``source``, or string-valued ``path`` keys and must not hide the later
    diagnostics array. An empty list is not treated as a hit either.

    Args:
        data: A decoded JSON value.

    Returns:
        True if ``data`` looks like Spectral's native JSON findings array.
    """
    if not isinstance(data, list) or not data:
        return False
    for item in data:
        if (
            isinstance(item, dict)
            and _SPECTRAL_IDENTITY_KEYS.intersection(item)
            and (
                isinstance(item.get("path"), list)
                or isinstance(item.get("range"), dict)
            )
        ):
            return True
    return False


def has_spectral_json_payload(output: str | None) -> bool:
    """Return whether output contains a valid Spectral JSON array.

    A clean Spectral run emits ``[]`` and may append informational runner text
    to the same stream. Finding runs emit a non-empty Spectral-shaped array.
    This helper distinguishes both valid forms from malformed successful output.

    Args:
        output: Raw Spectral standard output, possibly with runner noise.

    Returns:
        True when a clean or finding payload can be decoded.
    """
    if not output:
        return False
    decoder = json.JSONDecoder()
    idx = output.find("[")
    while idx != -1:
        try:
            candidate, _ = decoder.raw_decode(output, idx)
        except (json.JSONDecodeError, ValueError):
            idx = output.find("[", idx + 1)
            continue
        if isinstance(candidate, list) and (
            not candidate or _looks_like_spectral_payload(candidate)
        ):
            return True
        idx = output.find("[", idx + 1)
    return False


def _one_based_offset(value: object) -> int:
    """Convert a zero-based Spectral offset to a 1-based location, or 0.

    Args:
        value: Decoded JSON value for ``line`` or ``character``.

    Returns:
        1-based location when ``value`` is a non-negative int, otherwise 0
        (unknown). JSON null and missing keys must not become line/column 1.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value + 1


def parse_spectral_output(output: str | None) -> list[SpectralIssue]:
    """Parse Spectral JSON output into a list of SpectralIssue objects.

    Args:
        output: The raw JSON output from ``spectral lint --format json``.

    Returns:
        List of SpectralIssue objects. Returns an empty list for empty,
        null, or malformed input.
    """
    issues: list[SpectralIssue] = []

    if not output or not output.strip():
        return issues

    # Spectral may emit non-JSON preamble (e.g. a missing-ruleset warning or
    # a bracketed "[Warning] ..." stderr line merged into the stream) before
    # the JSON array. Try each "[" as a potential array start and take the
    # first position that decodes as a Spectral findings payload.
    data: Any = None
    idx = output.find("[")
    decoder = json.JSONDecoder()
    while idx != -1:
        try:
            candidate, _ = decoder.raw_decode(output, idx)
        except (json.JSONDecodeError, ValueError):
            idx = output.find("[", idx + 1)
            continue
        if _looks_like_spectral_payload(candidate):
            data = candidate
            break
        idx = output.find("[", idx + 1)
    if data is None:
        logger.debug("No valid Spectral JSON array found in output")
        return issues

    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            issue = _parse_entry(entry)
            if issue is not None:
                issues.append(issue)
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Failed to parse Spectral finding: {e}")
            continue

    return issues


def _parse_entry(entry: dict[str, Any]) -> SpectralIssue | None:
    """Parse a single Spectral finding into a SpectralIssue.

    Args:
        entry: A single finding object from Spectral's JSON array.

    Returns:
        SpectralIssue if parsing succeeds, otherwise None.
    """
    # ``or ""`` guards explicit JSON nulls: str(None) would fabricate the
    # literal string "None" as a filename, rule code, or message.
    code = str(entry.get("code") or "")
    message = str(entry.get("message") or "")
    file_path = str(entry.get("source") or "")
    if not message and not code:
        return None

    # Severity: integer diagnostic level mapped to a lintro severity string.
    raw_severity = entry.get("severity", 1)
    severity = _SEVERITY_BY_LEVEL.get(
        raw_severity if isinstance(raw_severity, int) else 1,
        "warning",
    )

    # JSON path: array of segments pointing at the offending node. Joined with
    # "." for display; empty for document-level findings.
    raw_path = entry.get("path", [])
    if isinstance(raw_path, list):
        path = ".".join(str(segment) for segment in raw_path if segment is not None)
    else:
        path = ""

    # Range: zero-based line/character offsets -> one-based line/column.
    # Missing or null offsets stay 0 (unknown), not 1:1.
    line = 0
    column = 0
    range_obj = entry.get("range")
    if isinstance(range_obj, dict):
        start = range_obj.get("start")
        if isinstance(start, dict):
            line = _one_based_offset(start.get("line"))
            column = _one_based_offset(start.get("character"))

    return SpectralIssue(
        file=file_path,
        line=line,
        column=column,
        message=message,
        code=code,
        severity=severity,
        path=path,
    )
