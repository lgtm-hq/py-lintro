"""Pytest configuration for golangci-lint plugin tests."""

from __future__ import annotations

import shutil
from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.golangci_lint import GolangciLintPlugin

# Real golangci-lint JSON output with two findings, used to drive mocked runs.
GOLANGCI_JSON_TWO_ISSUES = (
    '{"Issues":[{"FromLinter":"errcheck",'
    '"Text":"Error return value of `os.Open` is not checked","Severity":"",'
    '"Pos":{"Filename":"main.go","Line":9,"Column":9}},'
    '{"FromLinter":"ineffassign","Text":"ineffectual assignment to x",'
    '"Severity":"","Pos":{"Filename":"main.go","Line":10,"Column":2}}],'
    '"Report":{"Linters":[]}}'
)

GOLANGCI_JSON_ONE_ISSUE = (
    '{"Issues":[{"FromLinter":"errcheck",'
    '"Text":"Error return value of `os.Open` is not checked","Severity":"",'
    '"Pos":{"Filename":"main.go","Line":9,"Column":9}}],'
    '"Report":{"Linters":[]}}'
)

GOLANGCI_JSON_NO_ISSUES = '{"Issues":[],"Report":{"Linters":[]}}'


@pytest.fixture
def golangci_lint_plugin() -> Generator[GolangciLintPlugin, None, None]:
    """Provide a GolangciLintPlugin with the version check mocked out.

    Yields:
        GolangciLintPlugin: Instance whose version verification is bypassed.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield GolangciLintPlugin()


def golangci_lint_available() -> bool:
    """Return whether golangci-lint and the Go toolchain are on PATH.

    golangci-lint requires the Go toolchain to build and analyze a module, so
    integration tests are gated on both being present.

    Returns:
        bool: True when both golangci-lint and go can be invoked.
    """
    return shutil.which("golangci-lint") is not None and shutil.which("go") is not None
