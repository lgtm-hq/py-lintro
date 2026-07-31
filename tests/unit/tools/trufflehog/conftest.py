"""Pytest configuration for trufflehog plugin tests."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

from lintro.models.core.tool_result import ToolResult
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


def run_check_with_stderr(
    *,
    plugin: TrufflehogPlugin,
    tmp_path: Path,
    stderr: str,
    stdout: str = "",
    returncode: int = 0,
    scan_path: Path | None = None,
) -> ToolResult:
    """Run a check over one throwaway module with a canned subprocess result.

    Args:
        plugin: The plugin under test.
        tmp_path: Temporary directory to hold the scanned module.
        stderr: Captured standard error to feed the plugin.
        stdout: Captured standard output to feed the plugin.
        returncode: Process exit code to feed the plugin.
        scan_path: File to scan. When omitted, a throwaway ``module.py`` is
            created under ``tmp_path``. Pass this when the stderr payload
            references the scanned path so the two stay coupled.

    Returns:
        The ToolResult produced by the check.
    """
    if scan_path is None:
        scan_path = tmp_path / "module.py"
        scan_path.write_text('"""Module."""\n')

    with patch.object(
        plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        ),
    ):
        return plugin.check([str(scan_path)], {})


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
