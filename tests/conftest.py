"""Global test configuration for pytest."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from lintro.plugins.discovery import discover_all_tools
from lintro.utils.path_utils import normalize_file_path_for_display

# Ensure stable docker builds under pytest-xdist by disabling BuildKit, which
# can be flaky with concurrent builds/tags on some local setups.
os.environ.setdefault("DOCKER_BUILDKIT", "0")


@pytest.fixture(scope="session", autouse=True)
def _discover_tools() -> None:
    """Discover and register all tool plugins before tests run.

    This ensures that ToolRegistry.get() works in all tests by loading
    the builtin tool definitions and any external plugins.
    """
    discover_all_tools()


@pytest.fixture(autouse=True)
def _clear_tool_snapshot_cache() -> Iterator[None]:
    """Clear capability-probe caches so tests do not share stale snapshots."""
    from lintro.tools.core.snapshots import clear_snapshot_cache, set_force_fresh_probes

    clear_snapshot_cache()
    set_force_fresh_probes(False)
    yield
    clear_snapshot_cache()
    set_force_fresh_probes(False)


"""Shared fixtures used across tests in this repository."""


@pytest.fixture
def cli_runner() -> CliRunner:
    """Provide a Click CLI runner for testing.

    Returns:
        CliRunner: CLI runner for invoking commands.
    """
    return CliRunner()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory for testing.

    Yields:
        Path: Path to the temporary directory.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def ruff_violation_file(temp_dir: Path) -> str:
    """Copy the ruff_violations.py sample to a temp directory.

    return normalized path.

    Args:
        temp_dir: Temporary directory fixture.

    Returns:
        str: Normalized path to the copied ruff_violations.py file.
    """
    src = Path("test_samples/tools/python/ruff/ruff_e501_f401_violations.py").resolve()
    dst = temp_dir / "ruff_violations.py"
    shutil.copy(src, dst)
    result: str = normalize_file_path_for_display(str(dst))
    return result


@pytest.fixture
def skip_config_injection(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Skip Lintro config injection for tests.

    Sets LINTRO_SKIP_CONFIG_INJECTION environment variable to disable
    config injection during tests that need to test native tool configs.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        None: This fixture is used for its side effect only.
    """
    monkeypatch.setenv("LINTRO_SKIP_CONFIG_INJECTION", "1")
    yield


@pytest.fixture(autouse=True)
def clear_logging_handlers() -> Iterator[None]:
    """Clear logging handlers before each test.

    Yields:
        None: This fixture is used for its side effect only.
    """
    import logging

    logging.getLogger().handlers.clear()
    yield
