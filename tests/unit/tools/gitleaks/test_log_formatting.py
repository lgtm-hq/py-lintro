"""Gitleaks log records interpolate their values under loguru (#2400).

loguru formats with ``str.format`` braces, so a ``%s`` placeholder is logged
verbatim and the values passed with it are dropped.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.gitleaks.definition import GitleaksPlugin


@pytest.fixture
def error_messages() -> Generator[list[str], None, None]:
    """Capture loguru messages logged at ERROR or above.

    Yields:
        list[str]: The formatted messages.
    """
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message).strip()),
        level="ERROR",
        format="{message}",
    )
    try:
        yield messages
    finally:
        logger.remove(sink_id)


@pytest.mark.parametrize(
    ("returncode", "report", "expected"),
    [
        pytest.param(
            2,
            "[]",
            "Gitleaks exited with code 2: bad config",
            id="non-zero-exit",
        ),
        pytest.param(
            0,
            '{"not": "a list"}',
            "Gitleaks output was not a JSON array (got dict).",
            id="non-array-report",
        ),
    ],
)
def test_error_logs_interpolate_values(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
    error_messages: list[str],
    returncode: int,
    report: str,
    expected: str,
) -> None:
    """Failure logs name the exit code, stderr and report type.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
        error_messages: Captured ERROR log messages.
        returncode: Exit code the fake gitleaks returns.
        report: JSON the fake gitleaks writes to its report file.
        expected: The fully interpolated log message.
    """
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n")

    def fake_run(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        report_path = cmd[cmd.index("--report-path") + 1]
        Path(report_path).write_text(report, encoding="utf-8")
        return SubprocessResult(
            returncode=returncode,
            stdout="",
            stderr="bad config",
            output="bad config",
        )

    with patch.object(gitleaks_plugin, "_run_subprocess_result", side_effect=fake_run):
        result = gitleaks_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(error_messages).contains(expected)
