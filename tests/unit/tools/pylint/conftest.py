"""Shared fixtures for the pylint plugin unit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.pylint.definition import PylintPlugin

#: Body of the R0801 message pylint emits for a two-file clone set.
R0801_MESSAGE = (
    "Similar lines in 2 files\n"
    "==first:[12:27]\n"
    "==second:[12:27]\n"
    "    totals = []\n"
    "    return totals"
)

CLEAN_REPORT = json.dumps({"messages": [], "statistics": {"score": 10.0}})

DUPLICATE_REPORT = json.dumps(
    {
        "messages": [
            {
                "type": "refactor",
                "symbol": "duplicate-code",
                "message": R0801_MESSAGE,
                "messageId": "R0801",
                "line": 1,
                "column": 0,
                "path": "second.py",
                "absolutePath": "/repo/second.py",
            },
        ],
        "statistics": {"score": 9.5},
    },
)


def make_result(
    returncode: int,
    stdout: str,
    stderr: str = "",
) -> SubprocessResult:
    """Build a SubprocessResult standing in for a real pylint run.

    Args:
        returncode: Exit status pylint would return (a bit field).
        stdout: Captured standard output.
        stderr: Captured standard error.

    Returns:
        A SubprocessResult with the given streams.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=f"{stdout}{stderr}",
    )


@pytest.fixture
def pylint_plugin() -> PylintPlugin:
    """Provide a fresh pylint plugin instance.

    Returns:
        A ``PylintPlugin`` instance.
    """
    return PylintPlugin()


@pytest.fixture
def clean_report() -> str:
    """Return a json2 report with no messages.

    Returns:
        Raw pylint stdout.
    """
    return CLEAN_REPORT


@pytest.fixture
def duplicate_report() -> str:
    """Return a json2 report carrying one R0801 message.

    Returns:
        Raw pylint stdout.
    """
    return DUPLICATE_REPORT


@pytest.fixture
def configured_project(tmp_path: Path) -> Path:
    """Create a project whose ``pyproject.toml`` configures pylint.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the project root.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pylint.main]\ndisable = ["all"]\nenable = ["duplicate-code"]\n',
        encoding="utf-8",
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def unconfigured_project(tmp_path: Path) -> Path:
    """Create a project with no pylint configuration anywhere above it.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the project root.
    """
    project = tmp_path / "plain"
    project.mkdir()
    (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    return project
