"""TruffleHog output parser for secret detection findings."""

from __future__ import annotations

import json

from loguru import logger

from lintro.parsers.base_parser import validate_int_field, validate_str_field
from lintro.parsers.trufflehog.trufflehog_issue import TrufflehogIssue


def parse_trufflehog_output(output: str | None) -> list[TrufflehogIssue]:
    """Parse TruffleHog JSON output into TrufflehogIssue objects.

    TruffleHog emits newline-delimited JSON (JSONL): one JSON object per line,
    each representing a detected secret. It does not wrap results in a JSON
    array. Blank lines and non-result diagnostic lines are ignored.

    Args:
        output: Raw JSONL output string from trufflehog, or None.

    Returns:
        List of parsed secret detection findings. Returns an empty list if the
        output is empty or contains no parseable findings.
    """
    if not output or not output.strip():
        return []

    issues: list[TrufflehogIssue] = []

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as e:
            logger.debug(f"Skipping non-JSON trufflehog line: {e}")
            continue

        if not isinstance(item, dict):
            logger.debug("Skipping non-dict item in trufflehog output")
            continue

        # Skip diagnostic/log lines (they carry a "level" key and no metadata).
        if "SourceMetadata" not in item:
            continue

        try:
            issue = _parse_single_finding(item)
            if issue is not None:
                issues.append(issue)
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Failed to parse trufflehog finding: {e}")
            continue

    return issues


def _extract_location(item: dict[str, object]) -> tuple[str, int]:
    """Extract the file path and line number from a finding's SourceMetadata.

    TruffleHog nests location data under ``SourceMetadata.Data.<source>`` where
    ``<source>`` depends on the scan mode (e.g. ``Filesystem`` or ``Git``). This
    prefers filesystem metadata but falls back to any source that exposes a
    ``file`` key.

    Args:
        item: Dictionary representing a single finding from trufflehog JSON.

    Returns:
        Tuple of (file path, line number). Empty string / 0 when unavailable.
    """
    metadata = item.get("SourceMetadata")
    if not isinstance(metadata, dict):
        return "", 0

    data = metadata.get("Data")
    if not isinstance(data, dict):
        return "", 0

    # Prefer the filesystem source; otherwise use the first source with a file.
    sources = list(data.values())
    filesystem = data.get("Filesystem")
    if isinstance(filesystem, dict):
        sources = [filesystem, *[s for s in sources if s is not filesystem]]

    for source in sources:
        if not isinstance(source, dict):
            continue
        file_path = validate_str_field(
            value=source.get("file"),
            field_name="file",
        )
        if file_path:
            line = validate_int_field(
                value=source.get("line"),
                field_name="line",
            )
            return file_path, line

    return "", 0


def _parse_single_finding(item: dict[str, object]) -> TrufflehogIssue | None:
    """Parse a single trufflehog finding into a TrufflehogIssue.

    Args:
        item: Dictionary representing a single finding from trufflehog JSON.

    Returns:
        TrufflehogIssue if parsing succeeds, None otherwise.
    """
    file_path, line = _extract_location(item)
    if not file_path:
        logger.debug("Skipping trufflehog finding with no file location")
        return None

    detector_name = validate_str_field(
        value=item.get("DetectorName"),
        field_name="DetectorName",
    )
    detector_type = validate_int_field(
        value=item.get("DetectorType"),
        field_name="DetectorType",
    )
    description = validate_str_field(
        value=item.get("DetectorDescription"),
        field_name="DetectorDescription",
    )
    decoder_name = validate_str_field(
        value=item.get("DecoderName"),
        field_name="DecoderName",
    )
    raw = validate_str_field(
        value=item.get("Raw"),
        field_name="Raw",
    )
    redacted = validate_str_field(
        value=item.get("Redacted"),
        field_name="Redacted",
    )
    source_type = validate_int_field(
        value=item.get("SourceType"),
        field_name="SourceType",
    )
    source_name = validate_str_field(
        value=item.get("SourceName"),
        field_name="SourceName",
    )

    verified_raw = item.get("Verified")
    verified = verified_raw is True

    # ExtraData is a free-form string map (rotation guides, versions, etc.).
    extra_data: dict[str, str] = {}
    rotation_guide = ""
    extra_raw = item.get("ExtraData")
    if isinstance(extra_raw, dict):
        for key, value in extra_raw.items():
            if isinstance(key, str) and isinstance(value, str):
                extra_data[key] = value
        rotation_guide = extra_data.get("rotation_guide", "")

    return TrufflehogIssue(
        file=file_path,
        line=line,
        column=0,
        detector_name=detector_name,
        detector_type=detector_type,
        description=description,
        verified=verified,
        decoder_name=decoder_name,
        raw=raw,
        redacted=redacted,
        source_type=source_type,
        source_name=source_name,
        rotation_guide=rotation_guide,
        extra_data=extra_data,
    )
