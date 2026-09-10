"""Utility functions for pytest tool implementation.

This module contains helper functions extracted from tool_pytest.py to improve
maintainability and reduce file size. Functions are organized by category:
- JUnit XML processing
- Environment and system utilities
- Flaky test detection
"""

import json
import os
import xml.etree.ElementTree  # nosec B405 - only used for exception type, parsing uses defusedxml
from pathlib import Path

from loguru import logger

from lintro.enums.pytest_enums import PytestParallelPreset
from lintro.parsers.pytest.pytest_issue import PytestIssue

# Constants for flaky test detection
PYTEST_FLAKY_CACHE_FILE: str = ".pytest_cache/lintro_flaky_tests.json"
PYTEST_FLAKY_MIN_RUNS: int = 3  # Minimum runs before detecting flaky tests
PYTEST_FLAKY_FAILURE_RATE: float = 0.3  # Consider flaky if fails >= 30% but < 100%


def extract_all_test_results_from_junit(junitxml_path: str) -> dict[str, str] | None:
    """Extract all test results from JUnit XML file.

    Args:
        junitxml_path: Path to JUnit XML file.

    Returns:
        dict[str, str] | None: Dictionary mapping node_id to status
            (PASSED/FAILED/ERROR), or None if file doesn't exist or can't be parsed.
    """
    xml_path = Path(junitxml_path)
    if not xml_path.exists():
        return None

    try:
        from defusedxml import ElementTree

        tree = ElementTree.parse(xml_path)
        root = tree.getroot()
        if root is None:
            return None

        test_results: dict[str, str] = {}

        for testcase in root.findall(".//testcase"):
            file_path = testcase.get("file", "")
            class_name = testcase.get("classname", "")
            test_name = testcase.get("name", "")
            if file_path:
                if class_name:
                    node_id = f"{file_path}::{class_name}::{test_name}"
                else:
                    node_id = f"{file_path}::{test_name}"
            else:
                node_id = f"{class_name}::{test_name}" if class_name else test_name

            # Determine status
            if testcase.find("failure") is not None:
                status = "FAILED"
            elif testcase.find("error") is not None:
                status = "ERROR"
            elif testcase.find("skipped") is not None:
                status = "SKIPPED"
            else:
                status = "PASSED"

            test_results[node_id] = status

        return test_results
    except (
        ImportError,
        OSError,
        xml.etree.ElementTree.ParseError,
        KeyError,
        AttributeError,
    ) as e:
        logger.debug(f"Failed to parse JUnit XML for all tests: {e}")
        return None


def get_cpu_count() -> int:
    """Get the number of available CPU cores.

    Returns:
        int: Number of CPU cores, minimum 1.
    """
    try:
        import multiprocessing

        return max(1, multiprocessing.cpu_count())
    except (OSError, ValueError, NotImplementedError):
        return 1


def get_parallel_workers_from_preset(
    preset: str,
    test_count: int | None = None,
) -> str:
    """Convert parallel preset to worker count.

    Args:
        preset: Preset name (auto, small, medium, large) or number as string.
        test_count: Optional test count for dynamic presets.

    Returns:
        str: Worker count string for pytest-xdist (-n flag).

    Raises:
        ValueError: If preset is invalid.
    """
    preset_lower = preset.lower()

    if preset_lower == PytestParallelPreset.AUTO:
        return "auto"
    elif preset_lower == PytestParallelPreset.SMALL:
        return "2"
    elif preset_lower == PytestParallelPreset.MEDIUM:
        return "4"
    elif preset_lower == PytestParallelPreset.LARGE:
        cpu_count = get_cpu_count()
        # Use up to 8 workers for large suites, but not more than CPU count
        return str(min(8, cpu_count))
    elif preset_lower.isdigit():
        # Already a number, return as-is
        return preset
    else:
        raise ValueError(
            f"Invalid parallel preset: {preset}. "
            "Must be one of: auto, small, medium, large, or a number",
        )


def is_ci_environment() -> bool:
    """Detect if running in a CI/CD environment.

    Checks for common CI environment variables:
    - CI (generic CI indicator)
    - GITHUB_ACTIONS (GitHub Actions)
    - GITLAB_CI (GitLab CI)
    - JENKINS_URL (Jenkins)
    - CIRCLE_CI (CircleCI)
    - TRAVIS (Travis CI)
    - AZURE_HTTP_USER_AGENT (Azure DevOps)
    - TEAMCITY_VERSION (TeamCity)
    - BUILDKITE (Buildkite)
    - DRONE (Drone CI)

    Returns:
        bool: True if running in CI environment, False otherwise.
    """
    ci_indicators = [
        "CI",
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "JENKINS_URL",
        "CIRCLE_CI",
        "CIRCLECI",
        "TRAVIS",
        "AZURE_HTTP_USER_AGENT",
        "TEAMCITY_VERSION",
        "BUILDKITE",
        "DRONE",
    ]
    return any(
        os.environ.get(indicator, "").lower() not in ("", "false", "0")
        for indicator in ci_indicators
    )


def get_flaky_cache_path() -> Path:
    """Get the path to the flaky test cache file.

    Returns:
        Path: Path to the cache file.
    """
    cache_path = Path(PYTEST_FLAKY_CACHE_FILE)
    cache_path.parent.mkdir(exist_ok=True)
    return cache_path


def load_flaky_test_history() -> dict[str, dict[str, int]]:
    """Load flaky test history from cache file.

    Returns:
        dict[str, dict[str, int]]: Dictionary mapping test node_id to status counts.
        Format: {node_id: {"passed": count, "failed": count, "error": count}}
    """
    cache_path = get_flaky_cache_path()
    if not cache_path.exists():
        return {}

    try:
        with open(cache_path, encoding="utf-8") as f:
            data: dict[str, dict[str, int]] = json.load(f)
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Failed to load flaky test history: {e}")
        return {}


def save_flaky_test_history(history: dict[str, dict[str, int]]) -> None:
    """Save flaky test history to cache file.

    Args:
        history: Dictionary mapping test node_id to status counts.
    """
    cache_path = get_flaky_cache_path()
    try:
        # Ensure parent directory exists before writing
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except OSError as e:
        logger.debug(f"Failed to save flaky test history: {e}")


def compute_updated_flaky_test_history(
    issues: list[PytestIssue],
    all_test_results: dict[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Update flaky test history with current test results.

    Args:
        issues: List of parsed test issues (failures/errors).
        all_test_results: Optional dictionary mapping node_id to status for all tests.
                         If None, only tracks failures from issues.

    Returns:
        Dictionary mapping test node IDs to their pass/fail/error counts.
        Format: {node_id: {"passed": count, "failed": count, "error": count}}
    """
    history = load_flaky_test_history()

    # If we have full test results (e.g., from JUnit XML), use those
    if all_test_results:
        for node_id, status in all_test_results.items():
            if node_id not in history:
                history[node_id] = {"passed": 0, "failed": 0, "error": 0}

            if status == "FAILED":
                history[node_id]["failed"] += 1
            elif status == "ERROR":
                history[node_id]["error"] += 1
            elif status == "PASSED":
                history[node_id]["passed"] += 1
    else:
        # Only track failures from issues (simpler but less accurate)
        for issue in issues:
            # Skip Mock objects in tests - only process real PytestIssue objects
            if not isinstance(issue, PytestIssue):
                continue
            if issue.node_id and isinstance(issue.node_id, str):
                if issue.node_id not in history:
                    history[issue.node_id] = {"passed": 0, "failed": 0, "error": 0}

                if issue.test_status == "FAILED":
                    history[issue.node_id]["failed"] += 1
                elif issue.test_status == "ERROR":
                    history[issue.node_id]["error"] += 1

    return history
