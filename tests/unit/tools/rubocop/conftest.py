"""Pytest fixtures and helpers for RuboCop plugin tests."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.rubocop import RubocopPlugin


@pytest.fixture
def rubocop_plugin() -> RubocopPlugin:
    """Provide a RubocopPlugin instance for testing.

    Returns:
        A RubocopPlugin instance.
    """
    return RubocopPlugin()


def make_result(stdout: str, *, returncode: int = 1) -> SubprocessResult:
    """Build a SubprocessResult with a given stdout payload.

    Args:
        stdout: The standard output payload (JSON for RuboCop).
        returncode: The subprocess exit code (RuboCop uses 1 when offenses
            are found).

    Returns:
        A SubprocessResult mirroring separated stdout/stderr streams.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr="",
        output=stdout,
    )


def rubocop_json(offenses: list[dict[str, Any]], *, path: str = "app.rb") -> str:
    """Build RuboCop JSON output for a single file.

    Args:
        offenses: List of offense dictionaries.
        path: File path reported in the output.

    Returns:
        JSON string in RuboCop ``--format json`` shape.
    """
    return json.dumps(
        {
            "files": [{"path": path, "offenses": offenses}],
            "summary": {"offense_count": len(offenses)},
        },
    )


def offense(
    *,
    cop_name: str = "Style/StringLiterals",
    severity: str = "convention",
    correctable: bool = True,
    message: str = "Prefer single-quoted strings.",
) -> dict[str, Any]:
    """Build a minimal offense dict for plugin tests.

    Args:
        cop_name: Cop identifier.
        severity: Native severity.
        correctable: Whether RuboCop can autocorrect.
        message: Offense message.

    Returns:
        Offense dictionary.
    """
    return {
        "cop_name": cop_name,
        "severity": severity,
        "message": message,
        "correctable": correctable,
        "corrected": False,
        "location": {"start_line": 1, "start_column": 1},
    }


def make_ctx(tmp_path: Any, files: list[str]) -> MagicMock:
    """Build a mocked execution context for plugin tests.

    Args:
        tmp_path: Temporary directory used as the working directory.
        files: Relative file names to process.

    Returns:
        A MagicMock standing in for the prepared execution context.
    """
    ctx = MagicMock()
    ctx.should_skip = False
    ctx.early_result = None
    ctx.timeout = 60
    ctx.cwd = str(tmp_path)
    ctx.rel_files = files
    ctx.files = files
    return ctx
