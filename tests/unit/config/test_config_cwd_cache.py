"""Tests for cwd-aware pyproject.toml caching (#734)."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.utils.config import clear_pyproject_cache, load_pyproject


def test_load_pyproject_returns_different_data_per_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_pyproject returns different data after chdir to a new project.

    Args:
        tmp_path: Temporary directory for two project roots.
        monkeypatch: Pytest monkeypatch for chdir.
    """
    clear_pyproject_cache()

    # Project A
    dir_a = tmp_path / "project_a"
    dir_a.mkdir()
    (dir_a / "pyproject.toml").write_text(
        '[project]\nname = "alpha"\n',
    )

    # Project B
    dir_b = tmp_path / "project_b"
    dir_b.mkdir()
    (dir_b / "pyproject.toml").write_text(
        '[project]\nname = "beta"\n',
    )

    monkeypatch.chdir(dir_a)
    data_a = load_pyproject()
    assert_that(data_a["project"]["name"]).is_equal_to("alpha")

    # Switching cwd should pick up the other pyproject
    clear_pyproject_cache()
    monkeypatch.chdir(dir_b)
    data_b = load_pyproject()
    assert_that(data_b["project"]["name"]).is_equal_to("beta")


def test_load_pyproject_cwd_aware_without_cache_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_pyproject returns correct data per cwd without explicit cache clear.

    Args:
        tmp_path: Temporary directory for two project roots.
        monkeypatch: Pytest monkeypatch for chdir.
    """
    clear_pyproject_cache()

    dir_a = tmp_path / "project_a"
    dir_a.mkdir()
    (dir_a / "pyproject.toml").write_text('[project]\nname = "alpha"\n')

    dir_b = tmp_path / "project_b"
    dir_b.mkdir()
    (dir_b / "pyproject.toml").write_text('[project]\nname = "beta"\n')

    monkeypatch.chdir(dir_a)
    assert_that(load_pyproject()["project"]["name"]).is_equal_to("alpha")

    # No cache clear — cwd change alone should return different data
    monkeypatch.chdir(dir_b)
    assert_that(load_pyproject()["project"]["name"]).is_equal_to("beta")

    # Return to dir_a — should still get alpha from cache
    monkeypatch.chdir(dir_a)
    assert_that(load_pyproject()["project"]["name"]).is_equal_to("alpha")
