"""Pytest configuration and helpers for ktlint plugin tests."""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.ktlint import KtlintPlugin


@pytest.fixture
def ktlint_plugin() -> Generator[KtlintPlugin, None, None]:
    """Provide a KtlintPlugin instance with the version check mocked.

    Yields:
        KtlintPlugin: A plugin instance whose version verification is stubbed.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield KtlintPlugin()


def make_result(
    file_entries: list[dict[str, Any]] | None,
    *,
    returncode: int | None = None,
) -> SubprocessResult:
    """Build a SubprocessResult carrying ktlint JSON on stdout.

    Args:
        file_entries: Per-file entries to serialize as the JSON report, or
            None to represent an execution failure with no report.
        returncode: Explicit exit code. Defaults to 0 when there are no
            errors and 1 when any file entry carries errors.

    Returns:
        SubprocessResult with stdout populated (or empty on failure).
    """
    if file_entries is None:
        return SubprocessResult(
            returncode=returncode if returncode is not None else 2,
            stdout="",
            stderr="Error: JAVA_HOME is not set",
            output="Error: JAVA_HOME is not set",
        )

    has_errors = any(entry.get("errors") for entry in file_entries)
    code = returncode if returncode is not None else (1 if has_errors else 0)
    stdout = json.dumps(file_entries)
    return SubprocessResult(
        returncode=code,
        stdout=stdout,
        stderr="",
        output=stdout,
    )


def error(rule: str = "standard:colon-spacing") -> dict[str, Any]:
    """Build a single ktlint error dictionary.

    Args:
        rule: The ktlint rule id.

    Returns:
        Dictionary representing a single ktlint error.
    """
    return {
        "line": 2,
        "column": 15,
        "message": "Unexpected spacing",
        "rule": rule,
    }
