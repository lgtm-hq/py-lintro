"""Shared fixtures for trufflehog parser tests.

The sample payloads mirror real TruffleHog 3.x JSONL output captured from a
``trufflehog filesystem --json --no-verification`` run against fixtures that
contain only FAKE credentials.
"""

from __future__ import annotations

import json

import pytest


def make_finding(
    *,
    file: str = "config.py",
    line: int = 8,
    detector_name: str = "Github",
    detector_type: int = 8,
    verified: bool = False,
    raw: str = "ghp_examplefakeexamplefakeexamplefake1234",  # noqa: S107
    redacted: str = "",
    extra_data: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build a single TruffleHog finding dictionary.

    Args:
        file: Filesystem path recorded in ``SourceMetadata``.
        line: Line number recorded in ``SourceMetadata``.
        detector_name: Human-readable detector name.
        detector_type: Numeric detector type.
        verified: Whether the credential was verified as live.
        raw: The raw (fake) secret value.
        redacted: Pre-redacted representation, if any.
        extra_data: Optional detector metadata map.

    Returns:
        A dictionary shaped like one line of TruffleHog JSONL output.
    """
    return {
        "SourceMetadata": {"Data": {"Filesystem": {"file": file, "line": line}}},
        "SourceID": 1,
        "SourceType": 15,
        "SourceName": "trufflehog - filesystem",
        "DetectorType": detector_type,
        "DetectorName": detector_name,
        "DetectorDescription": (
            "GitHub is a platform for version control and collaboration."
        ),
        "DecoderName": "PLAIN",
        "Verified": verified,
        "VerificationFromCache": False,
        "Raw": raw,
        "RawV2": "",
        "Redacted": redacted,
        "ExtraData": (
            extra_data
            if extra_data is not None
            else {"rotation_guide": "https://howtorotate.com/docs/tutorials/github/"}
        ),
        "StructuredData": None,
    }


@pytest.fixture
def single_finding_output() -> str:
    """Return JSONL output containing exactly one unverified finding.

    Returns:
        A single-line JSONL string.
    """
    return json.dumps(make_finding())


@pytest.fixture
def log_line() -> str:
    """Return a diagnostic log line as TruffleHog writes to stderr/stdout.

    Returns:
        A JSON log line without ``SourceMetadata``.
    """
    return json.dumps(
        {
            "level": "info-0",
            "ts": "2026-07-06T22:44:15+02:00",
            "logger": "trufflehog",
            "msg": "finished scanning",
        },
    )
