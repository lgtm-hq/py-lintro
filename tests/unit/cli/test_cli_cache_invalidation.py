"""Tests for config-fingerprint based cache invalidation in the CLI.

These tests verify that the discovery and pyproject caches are cleared only
when the config inputs change between in-process invocations, rather than
unconditionally on every invocation (see issue #1231).
"""

import os
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


def test_no_cache_accepts_other_truthy_values(
    project_dir: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Truthy ``LINTRO_NO_CACHE`` values other than ``1`` still force a clear.

    Args:
        project_dir: Isolated project directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    calls = _spy_clear_functions(monkeypatch=monkeypatch)
    monkeypatch.setenv("LINTRO_NO_CACHE", "true")
    runner = CliRunner()
    first = runner.invoke(cli, ["list-tools"])
    second = runner.invoke(cli, ["list-tools"])
    assert_that(first.exit_code).is_equal_to(0)
    assert_that(second.exit_code).is_equal_to(0)
    assert_that(calls["discovery"][0]).is_equal_to(2)


def test_path_change_clears_discovery_cache(
    project_dir: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A ``PATH`` change between invokes clears the discovery cache.

    Args:
        project_dir: Isolated project directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.tools.core import runtime_discovery
    from lintro.tools.core.runtime_discovery import DiscoveredTool

    runner = CliRunner()
    first = runner.invoke(cli, ["list-tools"])
    assert_that(first.exit_code).is_equal_to(0)
    runtime_discovery._discovery_cache.tools["__sentinel__"] = DiscoveredTool(
        name="__sentinel__",
    )
    monkeypatch.setenv("PATH", os.environ.get("PATH", "") + ":/tmp/lintro-fake-bin")
    second = runner.invoke(cli, ["list-tools"])
    assert_that(second.exit_code).is_equal_to(0)
    assert_that("__sentinel__" in runtime_discovery._discovery_cache.tools).is_false()


def test_parent_config_change_clears_pyproject_cache(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Editing a parent ``.lintro-config.yaml`` invalidates cached pyproject data.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.utils import config as config_mod

    (tmp_path / "pyproject.toml").write_text('[tool.lintro]\ntool_order = "priority"\n')
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.setattr(cli_module, "_last_config_fingerprint", None)
    monkeypatch.delenv("LINTRO_NO_CACHE", raising=False)

    runner = CliRunner()
    first = runner.invoke(cli, ["list-tools"])
    assert_that(first.exit_code).is_equal_to(0)

    pyproject = (tmp_path / "pyproject.toml").resolve()
    config_mod._pyproject_data_cache[pyproject] = {"sentinel": True}
    (tmp_path / ".lintro-config.yaml").write_text("execution:\n  parallel: true\n")
    second = runner.invoke(cli, ["list-tools"])
    assert_that(second.exit_code).is_equal_to(0)
    assert_that(config_mod._pyproject_data_cache.get(pyproject)).is_not_equal_to(
        {"sentinel": True},
    )


def test_unchanged_config_keeps_pyproject_cache_data(
    project_dir: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A second invoke with unchanged inputs keeps cached pyproject data.

    Args:
        project_dir: Isolated project directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.utils import config as config_mod

    runner = CliRunner()
    first = runner.invoke(cli, ["list-tools"])
    assert_that(first.exit_code).is_equal_to(0)
    pyproject = (project_dir / "pyproject.toml").resolve()
    config_mod._pyproject_data_cache[pyproject] = {"sentinel": True}
    second = runner.invoke(cli, ["list-tools"])
    assert_that(second.exit_code).is_equal_to(0)
    assert_that(config_mod._pyproject_data_cache.get(pyproject)).is_equal_to(
        {"sentinel": True},
    )
