"""Tests for config-fingerprint based cache invalidation in the CLI.

These tests verify that the discovery and pyproject caches are cleared only
when the config inputs change between in-process invocations, rather than
unconditionally on every invocation (see issue #1231).
"""

from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from pytest import MonkeyPatch

import lintro.cli as cli_module
from lintro.cli import cli


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: MonkeyPatch) -> Path:
    """Create an isolated project directory and reset cache fingerprint state.

    Args:
        tmp_path: Path: Pytest-provided temporary directory.
        monkeypatch: MonkeyPatch: Pytest monkeypatch fixture.

    Returns:
        Path: The temporary project directory, set as the working directory.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.lintro]\ntool_order = "priority"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "_last_config_fingerprint", None)
    monkeypatch.delenv("LINTRO_NO_CACHE", raising=False)
    return tmp_path


def _spy_clear_functions(monkeypatch: MonkeyPatch) -> dict[str, list[int]]:
    """Replace the cache-clear functions with counting spies.

    Args:
        monkeypatch: MonkeyPatch: Pytest monkeypatch fixture.

    Returns:
        dict[str, list[int]]: Mapping of clear-function name to a single-element
        mutable counter list.
    """
    calls: dict[str, list[int]] = {"discovery": [0], "pyproject": [0]}

    def fake_clear_discovery() -> None:
        calls["discovery"][0] += 1

    def fake_clear_pyproject() -> None:
        calls["pyproject"][0] += 1

    monkeypatch.setattr(cli_module, "clear_discovery_cache", fake_clear_discovery)
    monkeypatch.setattr(cli_module, "clear_pyproject_cache", fake_clear_pyproject)
    return calls


def test_caches_reused_when_config_unchanged(
    project_dir: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Two invocations with unchanged config reuse caches on the second call.

    Args:
        project_dir: Path: Isolated project directory fixture.
        monkeypatch: MonkeyPatch: Pytest monkeypatch fixture.
    """
    calls = _spy_clear_functions(monkeypatch=monkeypatch)
    runner = CliRunner()

    first = runner.invoke(cli, ["list-tools"])
    assert_that(first.exit_code).is_equal_to(0)
    assert_that(calls["discovery"][0]).is_equal_to(1)
    assert_that(calls["pyproject"][0]).is_equal_to(1)

    second = runner.invoke(cli, ["list-tools"])
    assert_that(second.exit_code).is_equal_to(0)
    # No config change: the clear functions must not be called again.
    assert_that(calls["discovery"][0]).is_equal_to(1)
    assert_that(calls["pyproject"][0]).is_equal_to(1)


def test_caches_cleared_on_config_change(
    project_dir: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Modifying pyproject.toml between invocations clears the caches again.

    Args:
        project_dir: Path: Isolated project directory fixture.
        monkeypatch: MonkeyPatch: Pytest monkeypatch fixture.
    """
    calls = _spy_clear_functions(monkeypatch=monkeypatch)
    runner = CliRunner()

    first = runner.invoke(cli, ["list-tools"])
    assert_that(first.exit_code).is_equal_to(0)
    assert_that(calls["discovery"][0]).is_equal_to(1)
    assert_that(calls["pyproject"][0]).is_equal_to(1)

    # Change the config so both size and mtime differ from the first read.
    pyproject = project_dir / "pyproject.toml"
    pyproject.write_text(
        '[tool.lintro]\ntool_order = "alphabetical"\nextra = "changed value here"\n',
    )

    second = runner.invoke(cli, ["list-tools"])
    assert_that(second.exit_code).is_equal_to(0)
    assert_that(calls["discovery"][0]).is_equal_to(2)
    assert_that(calls["pyproject"][0]).is_equal_to(2)


def test_no_cache_escape_hatch(
    project_dir: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """LINTRO_NO_CACHE=1 forces cache clearing even when config is unchanged.

    Args:
        project_dir: Path: Isolated project directory fixture.
        monkeypatch: MonkeyPatch: Pytest monkeypatch fixture.
    """
    calls = _spy_clear_functions(monkeypatch=monkeypatch)
    monkeypatch.setenv("LINTRO_NO_CACHE", "1")
    runner = CliRunner()

    first = runner.invoke(cli, ["list-tools"])
    assert_that(first.exit_code).is_equal_to(0)
    assert_that(calls["discovery"][0]).is_equal_to(1)
    assert_that(calls["pyproject"][0]).is_equal_to(1)

    # Config is unchanged, but the escape hatch forces another clear.
    second = runner.invoke(cli, ["list-tools"])
    assert_that(second.exit_code).is_equal_to(0)
    assert_that(calls["discovery"][0]).is_equal_to(2)
    assert_that(calls["pyproject"][0]).is_equal_to(2)


def test_first_invocation_always_clears(
    project_dir: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """The first in-process invocation always clears caches to start fresh.

    Args:
        project_dir: Path: Isolated project directory fixture.
        monkeypatch: MonkeyPatch: Pytest monkeypatch fixture.
    """
    calls = _spy_clear_functions(monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["list-tools"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(calls["discovery"][0]).is_equal_to(1)
    assert_that(calls["pyproject"][0]).is_equal_to(1)


def test_fingerprint_changes_with_working_directory(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """The fingerprint differs across working directories.

    Args:
        tmp_path: Path: Pytest-provided temporary directory.
        monkeypatch: MonkeyPatch: Pytest monkeypatch fixture.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "pyproject.toml").write_text("[tool.lintro]\n")
    (dir_b / "pyproject.toml").write_text("[tool.lintro]\n")

    monkeypatch.chdir(dir_a)
    fingerprint_a = cli_module._compute_config_fingerprint()
    monkeypatch.chdir(dir_b)
    fingerprint_b = cli_module._compute_config_fingerprint()

    assert_that(fingerprint_a).is_not_equal_to(fingerprint_b)
