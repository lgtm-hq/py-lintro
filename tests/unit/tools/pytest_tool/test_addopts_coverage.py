"""Regression tests for pytest addopts coverage detection (issue #726).

A project whose pytest configuration ``addopts`` enable coverage forces coverage
to run even when lintro's own coverage options are unset. These tests reproduce
the conflict (banner said "disabled" while coverage ran) and lock in the fix:
lintro detects coverage in configuration ``addopts`` so the banner is accurate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.implementations.pytest.addopts_coverage import (
    config_addopts_enable_coverage,
)
from lintro.tools.implementations.pytest.pytest_config import PytestConfiguration
from lintro.tools.implementations.pytest.pytest_executor import PytestExecutor

# Minimal pytest.ini whose addopts force coverage on, mirroring the project
# configuration that triggered issue #726.
_PYTEST_INI_WITH_COV = (
    "[pytest]\n"
    "addopts =\n"
    "    --strict-markers\n"
    "    --cov=lintro\n"
    "    --cov-report=term-missing\n"
)

_PYTEST_INI_NO_COV = "[pytest]\naddopts =\n    --strict-markers\n    --durations=10\n"


@pytest.fixture
def conflicting_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fixture project whose pytest.ini addopts enable coverage.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.

    Returns:
        Path: Root of the fixture project (also the active working directory).
    """
    (tmp_path / "pytest.ini").write_text(_PYTEST_INI_WITH_COV, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_detects_coverage_in_pytest_ini(conflicting_project: Path) -> None:
    """Coverage flags in pytest.ini addopts are detected.

    Args:
        conflicting_project: Fixture project root with conflicting addopts.
    """
    assert_that(config_addopts_enable_coverage(conflicting_project)).is_true()


def test_no_coverage_when_pytest_ini_clean(tmp_path: Path) -> None:
    """A pytest.ini without --cov addopts reports no coverage.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pytest.ini").write_text(_PYTEST_INI_NO_COV, encoding="utf-8")
    assert_that(config_addopts_enable_coverage(tmp_path)).is_false()


def test_no_config_files_reports_no_coverage(tmp_path: Path) -> None:
    """An empty project (no pytest config) reports no coverage.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    assert_that(config_addopts_enable_coverage(tmp_path)).is_false()


def test_detects_coverage_in_pyproject_toml(tmp_path: Path) -> None:
    """Coverage flags in pyproject.toml pytest config are detected.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--cov=pkg --cov-report=xml"\n',
        encoding="utf-8",
    )
    assert_that(config_addopts_enable_coverage(tmp_path)).is_true()


def test_detects_coverage_in_pyproject_toml_list_addopts(tmp_path: Path) -> None:
    """Coverage flags in a list-style pyproject addopts are detected.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = ["-ra", "--cov=pkg"]\n',
        encoding="utf-8",
    )
    assert_that(config_addopts_enable_coverage(tmp_path)).is_true()


def test_detects_coverage_in_setup_cfg(tmp_path: Path) -> None:
    """Coverage flags in setup.cfg [tool:pytest] are detected.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "setup.cfg").write_text(
        "[tool:pytest]\naddopts =\n    --cov=pkg\n",
        encoding="utf-8",
    )
    assert_that(config_addopts_enable_coverage(tmp_path)).is_true()


def test_detects_coverage_in_tox_ini(tmp_path: Path) -> None:
    """Coverage flags in tox.ini [pytest] are detected.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "tox.ini").write_text(
        "[pytest]\naddopts = --cov=pkg\n",
        encoding="utf-8",
    )
    assert_that(config_addopts_enable_coverage(tmp_path)).is_true()


def test_banner_reports_enabled_when_addopts_force_coverage(
    conflicting_project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Banner reports coverage enabled when addopts force it (issue #726).

    Reproduces the reported conflict: lintro's own coverage options are unset,
    yet the project's pytest.ini addopts run coverage. The banner must reflect
    that coverage actually runs instead of contradicting it with "disabled".

    Args:
        conflicting_project: Fixture project root with conflicting addopts.
        capsys: Pytest capture fixture for stdout/stderr.
    """
    config = PytestConfiguration()
    executor = PytestExecutor(config=config, tool=None)

    executor.display_run_config(total_tests=1, target_files=["."])

    banner = capsys.readouterr().out
    assert_that(banner).contains("Coverage: enabled")
    assert_that(banner).does_not_contain("Coverage: disabled")


def test_banner_reports_disabled_without_coverage_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Banner reports coverage disabled when nothing enables it.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.
        capsys: Pytest capture fixture for stdout/stderr.
    """
    (tmp_path / "pytest.ini").write_text(_PYTEST_INI_NO_COV, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = PytestConfiguration()
    executor = PytestExecutor(config=config, tool=None)

    executor.display_run_config(total_tests=1, target_files=["."])

    banner = capsys.readouterr().out
    assert_that(banner).contains("Coverage: disabled")


def test_report_only_cov_flags_do_not_enable_coverage(tmp_path: Path) -> None:
    """Report/config-only --cov* flags do not count as enabling coverage.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --cov-report=xml --cov-config=.coveragerc --no-cov-on-fail\n",
        encoding="utf-8",
    )
    assert_that(config_addopts_enable_coverage(tmp_path)).is_false()


def test_detects_parent_pytest_ini_from_subdirectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent pytest.ini coverage is detected when run from a subdirectory.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.
    """
    (tmp_path / "pytest.ini").write_text(_PYTEST_INI_WITH_COV, encoding="utf-8")
    subdir = tmp_path / "pkg" / "nested"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    assert_that(config_addopts_enable_coverage()).is_true()


def test_nearer_config_without_coverage_stops_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nearer pytest.ini without coverage wins over a parent with coverage.

    Pytest stops at the first config it finds while walking upward, so a
    coverage-enabled parent must not make the banner report coverage when
    the nearer config has no --cov flags.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.
    """
    (tmp_path / "pytest.ini").write_text(_PYTEST_INI_WITH_COV, encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "pytest.ini").write_text(_PYTEST_INI_NO_COV, encoding="utf-8")
    nested = pkg / "tests"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert_that(config_addopts_enable_coverage()).is_false()


def test_sectionless_tox_ini_does_not_block_parent_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sectionless tox.ini must not stop the upward walk before parent config.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.
    """
    (tmp_path / "pytest.ini").write_text(_PYTEST_INI_WITH_COV, encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "tox.ini").write_text("[tox]\nenvlist = py313\n", encoding="utf-8")
    nested = pkg / "tests"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert_that(config_addopts_enable_coverage()).is_true()


def test_pyproject_wins_over_tox_ini_without_coverage(tmp_path: Path) -> None:
    """Same-directory pyproject.ini_options beat tox.ini coverage addopts.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-ra"\n',
        encoding="utf-8",
    )
    (tmp_path / "tox.ini").write_text(
        "[pytest]\naddopts = --cov=pkg\n",
        encoding="utf-8",
    )
    assert_that(config_addopts_enable_coverage(tmp_path)).is_false()


def test_pyproject_coverage_wins_over_setup_cfg_without_coverage(
    tmp_path: Path,
) -> None:
    """Same-directory pyproject coverage beats a setup.cfg without coverage.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--cov=pkg"\n',
        encoding="utf-8",
    )
    (tmp_path / "setup.cfg").write_text(
        "[tool:pytest]\naddopts = -ra\n",
        encoding="utf-8",
    )
    assert_that(config_addopts_enable_coverage(tmp_path)).is_true()
