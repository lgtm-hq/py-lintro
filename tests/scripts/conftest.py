"""Shared fixtures for CI script tests."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture
def temp_script_dir() -> Generator[Path, None, None]:
    """Create a temporary directory with script testing setup.

    Yields:
        Path: Path to the temporary directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        script_dir = Path(tmpdir)
        # Copy necessary files for script testing
        yield script_dir


@pytest.fixture
def mock_github_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Mock GitHub Actions environment variables.

    Args:
        monkeypatch: Pytest monkeypatch fixture for environment manipulation.

    Returns:
        dict: Dictionary of mocked environment variables.
    """
    env_vars = {
        # Fake test fixture token, not a real credential
        "GITHUB_TOKEN": "mock-token",  # nosec B105
        "GITHUB_REPOSITORY": "test/repo",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/123/merge",
        "GITHUB_SHA": "abc123def456",
    }

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    return env_vars
