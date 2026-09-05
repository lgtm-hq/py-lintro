"""Tests for coverage source resolution in the pytest command builder.

Lintro resolves the sources a project declares to ``coverage.py`` and spells
each one out as ``--cov=<source>``, falling back to ``--cov=.`` when a project
declares none. These tests lock in that resolution, its file precedence, and
that the emitted flags never leave a trailing test path to be swallowed as the
coverage source.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.definitions.pytest import PytestPlugin
from lintro.tools.implementations.pytest.coverage_source import (
    resolve_coverage_sources,
)
from lintro.tools.implementations.pytest.pytest_command_builder import (
    add_coverage_options,
    build_check_command,
)


@pytest.fixture(autouse=True)
def _clear_coverage_rcfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop an inherited COVERAGE_RCFILE so default discovery is deterministic.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to clear the env var.
    """
    monkeypatch.delenv("COVERAGE_RCFILE", raising=False)


def test_no_configuration_reports_no_source(tmp_path: Path) -> None:
    """A project without coverage configuration declares no source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    assert_that(resolve_coverage_sources(root=tmp_path)).is_empty()


def test_pyproject_source_is_detected(tmp_path: Path) -> None:
    """``[tool.coverage.run] source`` in pyproject declares a source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg"])


def test_pyproject_without_source_reports_no_source(tmp_path: Path) -> None:
    """A coverage section without a source key declares no source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.run]\nbranch = true\n",
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_empty()


def test_malformed_pyproject_reports_no_source(tmp_path: Path) -> None:
    """Unparseable TOML is treated as declaring no source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run\n", encoding="utf-8")

    assert_that(resolve_coverage_sources(root=tmp_path)).is_empty()


def test_coveragerc_source_is_detected(tmp_path: Path) -> None:
    """A ``.coveragerc`` ``[run] source`` declares a source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".coveragerc").write_text("[run]\nsource = pkg\n", encoding="utf-8")

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg"])


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

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg"])


def test_ancestor_configuration_is_ignored(tmp_path: Path) -> None:
    """Detection reads the working directory only, as coverage.py does.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert_that(resolve_coverage_sources(root=nested)).is_empty()


def test_first_matching_config_file_wins(tmp_path: Path) -> None:
    """A .coveragerc without a source shadows a pyproject that has one.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".coveragerc").write_text("[run]\nbranch = True\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_empty()


def test_config_file_without_coverage_settings_is_skipped(tmp_path: Path) -> None:
    """A setup.cfg with no coverage section does not stop the search.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = pkg\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg"])


def test_coverage_rcfile_env_var_selects_the_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COVERAGE_RCFILE overrides the default file precedence.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to set the env var.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )
    (tmp_path / "custom.cfg").write_text("[run]\nbranch = True\n", encoding="utf-8")
    monkeypatch.setenv("COVERAGE_RCFILE", "custom.cfg")

    assert_that(resolve_coverage_sources(root=tmp_path)).is_empty()


def test_coverage_rcfile_toml_source_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TOML file named by COVERAGE_RCFILE is read as TOML.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to set the env var.
    """
    rcfile = tmp_path / ".coveragerc.toml"
    rcfile.write_text('[tool.coverage.run]\nsource = ["pkg"]\n', encoding="utf-8")
    monkeypatch.setenv("COVERAGE_RCFILE", str(rcfile))

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg"])


def test_missing_coverage_rcfile_reports_no_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A COVERAGE_RCFILE that does not exist declares no source.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to set the env var.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("COVERAGE_RCFILE", "absent.cfg")

    assert_that(resolve_coverage_sources(root=tmp_path)).is_empty()


def test_builder_emits_each_configured_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every configured source is spelled out as its own ``--cov=`` flag.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg", "other"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    command: list[str] = []

    add_coverage_options(command, {"coverage_term_missing": True})

    assert_that(command).contains("--cov=pkg", "--cov=other")
    assert_that(command).does_not_contain("--cov", "--cov=.")


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


def test_coveragerc_toml_source_is_detected(tmp_path: Path) -> None:
    """A ``.coveragerc.toml`` outranks pyproject.toml.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".coveragerc.toml").write_text(
        '[run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.run]\nbranch = true\n",
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg"])


def test_report_only_config_shadows_a_lower_priority_source(tmp_path: Path) -> None:
    """A report-only .coveragerc is the active config, so no source is seen.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".coveragerc").write_text(
        "[report]\nshow_missing = True\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_empty()


def test_percent_in_ini_value_does_not_raise(tmp_path: Path) -> None:
    """A literal percent sign in an INI value is read without interpolation.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".coveragerc").write_text(
        "[run]\nsource = pkg\nrelative_files = True\ndata_file = cov%data\n",
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg"])


def test_source_options_are_merged_and_deduplicated(tmp_path: Path) -> None:
    """``source``, ``source_pkgs`` and ``source_dirs`` merge without repeats.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\nsource_pkgs = ["pkg", "extra"]\n',
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg", "extra"])


def test_ini_source_list_is_split(tmp_path: Path) -> None:
    """A multi-line INI ``source`` value yields one entry per source.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / ".coveragerc").write_text(
        "[run]\nsource =\n    pkg\n    other\n",
        encoding="utf-8",
    )

    assert_that(resolve_coverage_sources(root=tmp_path)).is_equal_to(["pkg", "other"])


def test_check_command_never_ends_with_a_bare_cov_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A threshold-only run with a trailing path keeps the path positional.

    ``pytest-cov`` declares ``--cov`` with ``nargs="?"``, so a bare ``--cov``
    followed by a test path would consume that path as the coverage source.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture used to switch the cwd.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["pkg"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    tool = PytestPlugin()
    tool.options["coverage_threshold"] = 80
    tool.exclude_patterns = []

    command, _ = build_check_command(tool=tool, files=["tests/unit"])

    # No bare "--cov" token exists, so nargs="?" has nothing to consume and the
    # trailing test path stays positional.
    assert_that(command).does_not_contain("--cov")
    assert_that(command).contains("--cov=pkg")
    assert_that(command[-1]).is_equal_to("tests/unit")
