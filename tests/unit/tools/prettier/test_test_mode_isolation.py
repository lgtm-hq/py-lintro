"""Tests for Prettier's ``LINTRO_TEST_MODE`` config/ignore isolation."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.tools.definitions.prettier import PRETTIER_TEST_MODE_ENV

if TYPE_CHECKING:
    from pathlib import Path

    from lintro.tools.definitions.prettier import PrettierPlugin


def test_isolation_args_empty_outside_test_mode(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns no extra args when test mode is off.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path used as the execution cwd.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv(PRETTIER_TEST_MODE_ENV, raising=False)

    args = prettier_plugin._test_mode_isolation_args(cwd=str(tmp_path))

    assert_that(args).is_empty()


def test_isolation_args_neutralize_ignore_file_discovery(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Points ``--ignore-path`` at the null device so fixtures stay visible.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path used as the execution cwd.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(PRETTIER_TEST_MODE_ENV, "1")

    args = prettier_plugin._test_mode_isolation_args(cwd=str(tmp_path))

    assert_that(args).contains("--ignore-path")
    assert_that(args[args.index("--ignore-path") + 1]).is_equal_to(os.devnull)


def test_isolation_args_force_no_config_without_local_config(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adds ``--no-config`` when the execution cwd has no Prettier config.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path used as the execution cwd.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(PRETTIER_TEST_MODE_ENV, "1")

    args = prettier_plugin._test_mode_isolation_args(cwd=str(tmp_path))

    assert_that(args).contains("--no-config")


def test_isolation_args_keep_local_config(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omits ``--no-config`` when the cwd ships its own Prettier config.

    Fixtures such as the Astro plugin tests rely on a cwd-local
    ``.prettierrc`` still being resolved under test mode.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path used as the execution cwd.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(PRETTIER_TEST_MODE_ENV, "1")
    (tmp_path / ".prettierrc").write_text("{}")

    args = prettier_plugin._test_mode_isolation_args(cwd=str(tmp_path))

    assert_that(args).does_not_contain("--no-config")
    assert_that(args).contains("--ignore-path")
