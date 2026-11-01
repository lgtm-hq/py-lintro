"""Type compatibility of the read-only tool status view."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from assertpy import assert_that
from mypy import api as mypy_api


def test_status_protocol_accepts_frozen_and_mutable_results(tmp_path: Path) -> None:
    """Both result representations satisfy the classifier's structural contract.

    Args:
        tmp_path: Isolated mypy cache directory.
    """
    source = dedent(
        """
        from dataclasses import dataclass

        from lintro.enums.tool_run_status import ToolOutcome, tool_run_status
        from lintro.models.core.tool_result import ToolResult

        @dataclass
        class PluginResult:
            success: bool = True
            skipped: bool = False
            timed_out: bool = False

        frozen: ToolOutcome = ToolResult(success=True)
        plugin = PluginResult()
        mutable: ToolOutcome = plugin
        tool_run_status(result=frozen, issue_count=0)
        plugin.success = False
        tool_run_status(result=mutable, issue_count=0)
        """,
    )
    stdout, stderr, status = mypy_api.run(
        [
            "--config-file",
            str(Path(__file__).resolve().parents[3] / "pyproject.toml"),
            "--follow-imports=silent",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "-c",
            source,
        ],
    )

    assert_that(status).described_as(stdout + stderr).is_zero()
