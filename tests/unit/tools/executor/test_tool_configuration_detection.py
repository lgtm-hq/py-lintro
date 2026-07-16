"""Tests for no-config language-scoped tool selection in get_tools_to_run.

Covers issue #1420: a no-config first run should scope the toolset to the
languages actually present in the project instead of firing every registered
tool. Explicit ``--tools`` and configured projects keep the full behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_loader import clear_config_cache
from lintro.utils.execution.tool_configuration import (
    format_detection_notice,
    get_tools_to_run,
)


def _write_python_project(root: Path) -> None:
    """Create a minimal Python-only project in *root*.

    Args:
        root: Directory to populate.
    """
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_config_cache() -> None:
    """Reset the config singleton before and after each test."""
    clear_config_cache()
    yield
    clear_config_cache()


def test_no_config_python_project_scopes_to_python_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No-config Python project scopes the toolset to Python-relevant tools.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_true()
    assert_that(result.detected_languages).contains("python")
    # Python tools survive scoping.
    assert_that(result.to_run).contains("ruff", "black", "mypy", "bandit")
    # Tools for absent languages are dropped entirely (no SKIP wall).
    assert_that(result.to_run).does_not_contain(
        "clippy",
        "rustfmt",
        "svelte-check",
        "sqlfluff",
    )
    skipped_names = [s.name for s in result.skipped]
    assert_that(skipped_names).does_not_contain("clippy", "svelte-check")


def test_explicit_tools_overrides_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit ``--tools`` bypasses language scoping.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools="yamllint", action="check")

    assert_that(result.scoped_by_detection).is_false()
    assert_that(result.to_run).is_equal_to(["yamllint"])


def test_explicit_all_is_not_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit ``--tools all`` runs the full toolset without scoping.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools="all", action="check")

    assert_that(result.scoped_by_detection).is_false()
    # Non-Python tools remain candidates under explicit "all".
    assert_that(result.to_run).contains("ruff", "clippy", "sqlfluff")


def test_configured_project_not_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A project with a config file keeps the full, unscoped behavior.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    (tmp_path / ".lintro-config.yaml").write_text(
        "tools:\n  ruff:\n    enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_false()
    # With a config present, non-Python tools are candidates (enabled by
    # default), not silently dropped by detection.
    assert_that(result.to_run).contains("ruff", "clippy", "sqlfluff")


def test_format_detection_notice_groups_by_language() -> None:
    """The notice groups tools by language and points at ``lintro init``."""
    notice = format_detection_notice(
        detected_languages=["python"],
        to_run=["ruff", "black", "mypy", "bandit"],
    )

    assert_that(notice).contains("No config found")
    assert_that(notice).contains("python:")
    assert_that(notice).contains("lintro init")
