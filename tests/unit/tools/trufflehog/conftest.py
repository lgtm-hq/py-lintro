"""Pytest configuration for trufflehog plugin tests."""

from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.trufflehog import TrufflehogPlugin


@pytest.fixture
def trufflehog_plugin() -> Generator[TrufflehogPlugin, None, None]:
    """Provide a TrufflehogPlugin instance with the version check mocked.

    Yields:
        TrufflehogPlugin: A plugin instance ready for testing.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield TrufflehogPlugin()


def make_subprocess_result(
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> SubprocessResult:
    """Build a SubprocessResult for mocking _run_subprocess_result.

    Args:
        stdout: Captured standard output.
        stderr: Captured standard error.
        returncode: Process exit code.

    Returns:
        A SubprocessResult with a combined display output.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=(stdout + stderr),
    )


def sample_finding_line(*, file: str, line: int = 8, verified: bool = False) -> str:
    """Build one line of TruffleHog JSONL output for a fake Github token.

    Args:
        file: The filesystem path to record in SourceMetadata.
        line: The line number to record.
        verified: Whether the finding is verified.

    Returns:
        A single-line JSONL string.
    """
    return json.dumps(
        {
            "SourceMetadata": {"Data": {"Filesystem": {"file": file, "line": line}}},
            "SourceType": 15,
            "SourceName": "trufflehog - filesystem",
            "DetectorType": 8,
            "DetectorName": "Github",
            "DecoderName": "PLAIN",
            "Verified": verified,
            "Raw": "ghp_examplefakeexamplefakeexamplefake1234",
            "Redacted": "",
            "ExtraData": {
                "rotation_guide": "https://howtorotate.com/docs/tutorials/github/",
            },
        },
    )
