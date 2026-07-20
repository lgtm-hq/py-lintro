"""Shared fixtures for integration tests."""

import os
import tempfile
from collections.abc import Callable, Generator
from pathlib import Path

import pytest


@pytest.fixture
def temp_project_dir() -> Generator[Path, None, None]:
    """Create a temporary directory structure for integration testing.

    Yields:
        Path: Path to the temporary project directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        # Create a basic project structure
        (project_dir / "pyproject.toml").write_text(
            """[tool.lintro]
line_length = 88

[tool.ruff]
line-length = 88
""",
        )
        (project_dir / "src").mkdir()
        (project_dir / "tests").mkdir()

        # Change to the temp directory for the test
        original_cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            yield project_dir
        finally:
            os.chdir(original_cwd)


@pytest.fixture
def lintro_test_mode(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set LINTRO_TEST_MODE=1 environment variable for tests.

    This disables config injection and other test-incompatible features.

    Args:
        monkeypatch: Pytest monkeypatch fixture for environment manipulation.

    Returns:
        str: The test mode value that was set.
    """
    monkeypatch.setenv("LINTRO_TEST_MODE", "1")
    return "1"


@pytest.fixture
def skip_if_tool_unavailable() -> Callable[[str], None]:
    """Skip test if required tool is not available in PATH.

    Returns:
        callable: Function that takes a tool_name (str) parameter and can be used
        to skip tests for unavailable tools.
    """

    def _skip_if_unavailable(tool_name: str) -> None:
        """Skip the current test if tool is not available.

        Args:
            tool_name: Name of the tool to check for availability.
        """
        import shutil

        if not shutil.which(tool_name):
            pytest.skip(f"Tool '{tool_name}' not available in PATH")

    return _skip_if_unavailable


@pytest.fixture
def get_plugin(lintro_test_mode: str) -> Callable[[str], object]:
    """Factory fixture to get a fresh plugin instance by name.

    This fixture ensures LINTRO_TEST_MODE is set before getting the plugin,
    which disables config injection and other test-incompatible features.

    Creates a fresh instance to avoid test pollution from shared state.

    Args:
        lintro_test_mode: The test mode fixture (dependency).

    Returns:
        callable: Function that takes a tool name and returns a fresh plugin instance.
    """
    from lintro.plugins.registry import ToolRegistry

    def _get_plugin(name: str) -> object:
        """Get a fresh plugin instance by name.

        Args:
            name: Name of the tool to get.

        Returns:
            A fresh plugin instance (not the cached singleton).

        Raises:
            KeyError: If the tool name is not registered.
        """
        name_lower = name.lower()
        # Get the class from the registry and create a new instance
        # to avoid test pollution from modified options on the cached instance
        ToolRegistry._ensure_discovered()
        if name_lower in ToolRegistry._deferred:
            ToolRegistry._materialize(name_lower)
        if name_lower not in ToolRegistry._tools:
            available = ", ".join(sorted(ToolRegistry._known_names()))
            raise KeyError(
                f"Unknown tool: {name_lower!r}. Available: {available or 'none'}",
            )
        plugin_class = ToolRegistry._tools[name_lower]
        return plugin_class()

    return _get_plugin
