"""Shared fixtures for pytest tool tests."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from lintro.parsers.pytest.pytest_issue import PytestIssue
from lintro.tools.implementations.pytest.pytest_config import PytestConfiguration
from lintro.tools.implementations.pytest.pytest_result_processor import (
    PytestResultProcessor,
)

if TYPE_CHECKING:
    from lintro.tools.definitions.pytest import PytestPlugin


@pytest.fixture
def mock_test_tool() -> MagicMock:
    """Provide a mock PytestPlugin instance for testing.

    Returns:
        MagicMock: Mock PytestPlugin instance.
    """
    tool = MagicMock()
    tool.definition.name = "pytest"
    tool.can_fix = False
    tool.options = {}
    tool._default_timeout = 300
    tool.config.priority = 90

    # Mock common methods
    tool._get_executable_command.return_value = ["pytest"]
    tool._verify_tool_version.return_value = None

    return tool


@contextmanager
def patch_pytest_tool_for_check(
    tool: PytestPlugin,
    *,
    run_subprocess_return: tuple[bool, str] = (True, "All tests passed"),
    prepare_execution_return: tuple[int, int, Any] = (10, 0, None),
) -> Generator[None]:
    """Context manager providing common patches for pytest check tests.

    Args:
        tool: PytestPlugin instance to patch.
        run_subprocess_return: Return value for _run_subprocess.
        prepare_execution_return: Return value for prepare_test_execution.

    Yields:
        None: Context manager for patches.
    """
    with (
        patch.object(tool, "_get_executable_command", return_value=["pytest"]),
        patch.object(tool, "_run_subprocess", return_value=run_subprocess_return),
        patch.object(tool, "_parse_output", return_value=[]),
        patch.object(
            tool.executor,
            "prepare_test_execution",
            return_value=prepare_execution_return,
        ),
    ):
        yield


@pytest.fixture
def sample_pytest_plugin() -> Generator[PytestPlugin, None, None]:
    """Create a PytestPlugin instance for testing.

    Uses mocks to avoid filesystem operations during initialization.

    Yields:
        PytestPlugin: A PytestPlugin instance.
    """
    from lintro.tools.definitions.pytest import PytestPlugin

    with (
        patch(
            "lintro.tools.definitions.pytest.load_lintro_ignore",
            return_value=[],
        ),
        patch(
            "lintro.tools.definitions.pytest.load_pytest_config",
            return_value={},
        ),
        patch(
            "lintro.tools.definitions.pytest.load_file_patterns_from_config",
            return_value=[],
        ),
    ):
        yield PytestPlugin()


@pytest.fixture
def sample_pytest_config() -> PytestConfiguration:
    """Create a PytestConfiguration instance for testing.

    Returns:
        A PytestConfiguration instance.
    """
    return PytestConfiguration()


@pytest.fixture
def result_processor() -> PytestResultProcessor:
    """Create a PytestResultProcessor instance for testing.

    Returns:
        A PytestResultProcessor instance.
    """
    return PytestResultProcessor()


@pytest.fixture
def mock_test_success_output() -> str:
    """Mock pytest output for successful test run.

    Returns:
        A string representing successful pytest output.
    """
    return "collected 10 items\n10 passed in 0.12s"


@pytest.fixture
def mock_test_failure_output() -> str:
    """Mock pytest output for test run with failures.

    Returns:
        A string representing failed pytest output.
    """
    return "collected 10 items\n1 failed, 9 passed in 0.15s"


@pytest.fixture
def mock_test_mixed_output() -> str:
    """Mock pytest output for test run with mixed results.

    Returns:
        A string representing mixed pytest output.
    """
    return "collected 20 items\n2 failed, 15 passed, 2 skipped, 1 error in 1.50s"


@pytest.fixture
def mock_test_json_success() -> str:
    """Mock pytest JSON report for successful tests.

    Returns:
        A JSON string representing successful pytest results.
    """
    data = {
        "tests": [
            {
                "file": "tests/test_example.py",
                "lineno": 10,
                "name": "test_success",
                "nodeid": "tests/test_example.py::test_success",
                "outcome": "passed",
                "duration": 0.05,
            },
            {
                "file": "tests/test_example.py",
                "lineno": 20,
                "name": "test_another",
                "nodeid": "tests/test_example.py::test_another",
                "outcome": "passed",
                "duration": 0.03,
            },
        ],
    }
    return json.dumps(data)


@pytest.fixture
def mock_test_json_failure() -> str:
    """Mock pytest JSON report for failed tests.

    Returns:
        A JSON string representing failed pytest results.
    """
    data = {
        "tests": [
            {
                "file": "tests/test_example.py",
                "lineno": 10,
                "name": "test_failure",
                "nodeid": "tests/test_example.py::test_failure",
                "outcome": "failed",
                "duration": 0.05,
                "call": {
                    "longrepr": "AssertionError: assert 1 == 2",
                },
            },
            {
                "file": "tests/test_example.py",
                "lineno": 20,
                "name": "test_success",
                "nodeid": "tests/test_example.py::test_success",
                "outcome": "passed",
                "duration": 0.03,
            },
        ],
    }
    return json.dumps(data)


@pytest.fixture
def mock_test_json_mixed() -> str:
    """Mock pytest JSON report for mixed test results.

    Returns:
        A JSON string representing mixed pytest results.
    """
    data = {
        "tests": [
            {
                "file": "tests/test_example.py",
                "lineno": 10,
                "name": "test_failure",
                "nodeid": "tests/test_example.py::test_failure",
                "outcome": "failed",
                "duration": 0.05,
                "call": {
                    "longrepr": "AssertionError: assert 1 == 2",
                },
            },
            {
                "file": "tests/test_example.py",
                "lineno": 20,
                "name": "test_error",
                "nodeid": "tests/test_example.py::test_error",
                "outcome": "error",
                "duration": 0.02,
                "call": {
                    "longrepr": "RuntimeError: Something went wrong",
                },
            },
            {
                "file": "tests/test_example.py",
                "lineno": 30,
                "name": "test_skipped",
                "nodeid": "tests/test_example.py::test_skipped",
                "outcome": "skipped",
                "duration": 0.0,
                "longrepr": "Skipped: Not implemented yet",
            },
            {
                "file": "tests/test_example.py",
                "lineno": 40,
                "name": "test_success",
                "nodeid": "tests/test_example.py::test_success",
                "outcome": "passed",
                "duration": 0.03,
            },
        ],
    }
    return json.dumps(data)


@pytest.fixture
def mock_test_junit_xml_success() -> str:
    """Mock pytest JUnit XML output for successful tests.

    Returns:
        A JUnit XML string representing successful pytest results.
    """
    return """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="2" errors="0" failures="0" skipped="0" time="0.08">
    <testcase classname="tests.test_example" name="test_success" file="tests/test_example.py" line="10" time="0.05"/>
    <testcase classname="tests.test_example" name="test_another" file="tests/test_example.py" line="20" time="0.03"/>
</testsuite>
"""


@pytest.fixture
def mock_test_junit_xml_failure() -> str:
    """Mock pytest JUnit XML output for failed tests.

    Returns:
        A JUnit XML string representing failed pytest results.
    """
    return """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="2" errors="0" failures="1" skipped="0" time="0.08">
    <testcase classname="tests.test_example" name="test_failure" file="tests/test_example.py" line="10" time="0.05">
        <failure message="AssertionError: assert 1 == 2">AssertionError: assert 1 == 2</failure>
    </testcase>
    <testcase classname="tests.test_example" name="test_success" file="tests/test_example.py" line="20" time="0.03"/>
</testsuite>
"""


@pytest.fixture
def mock_test_junit_xml_mixed() -> str:
    """Mock pytest JUnit XML output for mixed test results.

    Returns:
        A JUnit XML string representing mixed pytest results.
    """
    return """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="4" errors="1" failures="1" skipped="1" time="0.10">
    <testcase classname="tests.test_example" name="test_failure" file="tests/test_example.py" line="10" time="0.05">
        <failure message="AssertionError: assert 1 == 2">AssertionError: assert 1 == 2</failure>
    </testcase>
    <testcase classname="tests.test_example" name="test_error" file="tests/test_example.py" line="20" time="0.02">
        <error message="RuntimeError: Something went wrong">RuntimeError: Something went wrong</error>
    </testcase>
    <testcase classname="tests.test_example" name="test_skipped" file="tests/test_example.py" line="30" time="0.0">
        <skipped message="Not implemented yet">Not implemented yet</skipped>
    </testcase>
    <testcase classname="tests.test_example" name="test_success" file="tests/test_example.py" line="40" time="0.03"/>
</testsuite>
"""


@pytest.fixture
def sample_pytest_issues() -> list[PytestIssue]:
    """Create sample PytestIssue objects for testing.

    Returns:
        A list of sample PytestIssue objects.
    """
    return [
        PytestIssue(
            file="tests/test_example.py",
            line=10,
            test_name="test_failure",
            message="AssertionError: assert 1 == 2",
            test_status="FAILED",
            duration=0.05,
            node_id="tests/test_example.py::test_failure",
        ),
        PytestIssue(
            file="tests/test_example.py",
            line=20,
            test_name="test_error",
            message="RuntimeError: Something went wrong",
            test_status="ERROR",
            duration=0.02,
            node_id="tests/test_example.py::test_error",
        ),
        PytestIssue(
            file="tests/test_example.py",
            line=30,
            test_name="test_skipped",
            message="Not implemented yet",
            test_status="SKIPPED",
            duration=0.0,
            node_id="tests/test_example.py::test_skipped",
        ),
    ]


@pytest.fixture
def sample_passed_issues() -> list[PytestIssue]:
    """Create sample passed PytestIssue objects for testing.

    Returns:
        A list of PytestIssue objects with passed status.
    """
    return [
        PytestIssue(
            file="tests/test_example.py",
            line=10,
            test_name="test_success",
            message="",
            test_status="PASSED",
            duration=0.05,
            node_id="tests/test_example.py::test_success",
        ),
    ]


@pytest.fixture
def mock_subprocess_success() -> MagicMock:
    """Create a mock for successful subprocess execution.

    Returns:
        A MagicMock representing successful subprocess execution.
    """
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "collected 10 items\n10 passed in 0.12s"
    mock.stderr = ""
    return mock


@pytest.fixture
def mock_subprocess_failure() -> MagicMock:
    """Create a mock for failed subprocess execution.

    Returns:
        A MagicMock representing failed subprocess execution.
    """
    mock = MagicMock()
    mock.returncode = 1
    mock.stdout = "collected 10 items\n1 failed, 9 passed in 0.15s"
    mock.stderr = ""
    return mock
