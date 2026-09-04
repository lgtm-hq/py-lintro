"""Tests for coverage source detection in the pytest command builder.

Bare ``pytest --cov`` measures whatever ``coverage.py`` is configured to
measure, which only helps when the project declares a source. These tests lock
in that lintro emits bare ``--cov`` for projects that declare one and keeps the
historical ``--cov=.`` for projects that do not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.implementations.pytest.coverage_source import (
    coverage_source_configured,
)
from lintro.tools.implementations.pytest.pytest_command_builder import (
    add_coverage_options,
)


def test_no_configuration_reports_no_source(tmp_path: Path) -> None:
    """A project without coverage configuration declares no source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    assert_that(coverage_source_configured(root=tmp_path)).is_false()


def test_pyproject_source_is_detected(tmp_path: Path) -> None:
    """``[tool.coverage.run] source`` in pyproject declares a source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )

    assert_that(coverage_source_configured(root=tmp_path)).is_true()


def test_pyproject_without_source_reports_no_source(tmp_path: Path) -> None:
    """A coverage section without a source key declares no source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.run]\nbranch = true\n",
        encoding="utf-8",
    )

    assert_that(coverage_source_configured(root=tmp_path)).is_false()


def test_malformed_pyproject_reports_no_source(tmp_path: Path) -> None:
    """Unparseable TOML is treated as declaring no source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run\n", encoding="utf-8")

    assert_that(coverage_source_configured(root=tmp_path)).is_false()


def test_coveragerc_source_is_detected(tmp_path: Path) -> None:
    """A ``.coveragerc`` ``[run] source`` declares a source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".coveragerc").write_text("[run]\nsource = pkg\n", encoding="utf-8")

    assert_that(coverage_source_configured(root=tmp_path)).is_true()


@pytest.mark.parametrize("filename", ["setup.cfg", "tox.ini"])
def test_ini_coverage_run_source_is_detected(tmp_path: Path, filename: str) -> None:
    """``[coverage:run] source`` in setup.cfg/tox.ini declares a source.

    Args:
        tmp_path: Temporary directory provided by pytest.
        filename: Configuration file name under test.
    """
    (tmp_path / filename).write_text(
        "[coverage:run]\nsource = pkg\n",
        encoding="utf-8",
    )

    assert_that(coverage_source_configured(root=tmp_path)).is_true()


def test_parent_directory_source_is_detected(tmp_path: Path) -> None:
    """Detection walks up to an ancestor that declares a source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert_that(coverage_source_configured(root=nested)).is_true()


def test_builder_emits_bare_cov_when_source_is_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured source makes the builder emit bare ``--cov``.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    command: list[str] = []

    add_coverage_options(command, {"coverage_term_missing": True})

    assert_that(command).contains("--cov")
    assert_that(command).does_not_contain("--cov=.")


def test_builder_falls_back_to_cov_dot_without_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No configured source keeps the historical ``--cov=.`` flag.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.
    """
    monkeypatch.chdir(tmp_path)
    command: list[str] = []

    add_coverage_options(command, {"coverage_term_missing": True})

    assert_that(command).contains("--cov=.")
