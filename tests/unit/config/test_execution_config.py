"""Tests for lintro.config.execution_config module."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.config.execution_config import ExecutionConfig, _get_default_max_workers


def test_execution_config_default_enabled_tools() -> None:
    """ExecutionConfig has empty enabled_tools by default."""
    config = ExecutionConfig()
    assert_that(config.enabled_tools).is_empty()


def test_execution_config_default_fail_fast() -> None:
    """ExecutionConfig has fail_fast disabled by default."""
    config = ExecutionConfig()
    assert_that(config.fail_fast).is_false()


def test_execution_config_default_parallel() -> None:
    """ExecutionConfig has parallel enabled by default."""
    config = ExecutionConfig()
    assert_that(config.parallel).is_true()


def test_execution_config_max_workers_uses_cpu_count() -> None:
    """ExecutionConfig max_workers defaults to CPU count."""
    config = ExecutionConfig()
    expected = max(1, min(os.cpu_count() or 4, 32))
    assert_that(config.max_workers).is_equal_to(expected)


def test_execution_config_set_enabled_tools() -> None:
    """ExecutionConfig accepts enabled_tools list."""
    config = ExecutionConfig(enabled_tools=["ruff", "black"])
    assert_that(config.enabled_tools).contains("ruff", "black")


def test_execution_config_set_fail_fast() -> None:
    """ExecutionConfig accepts fail_fast=True."""
    config = ExecutionConfig(fail_fast=True)
    assert_that(config.fail_fast).is_true()


def test_execution_config_set_parallel_false() -> None:
    """ExecutionConfig accepts parallel=False."""
    config = ExecutionConfig(parallel=False)
    assert_that(config.parallel).is_false()


def test_execution_config_set_max_workers() -> None:
    """ExecutionConfig accepts custom max_workers."""
    config = ExecutionConfig(max_workers=8)
    assert_that(config.max_workers).is_equal_to(8)


def test_execution_config_max_workers_minimum() -> None:
    """ExecutionConfig enforces minimum max_workers of 1."""
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        ExecutionConfig(max_workers=0)


def test_execution_config_max_workers_maximum() -> None:
    """ExecutionConfig enforces maximum max_workers of 32."""
    with pytest.raises(ValueError, match="less than or equal to 32"):
        ExecutionConfig(max_workers=100)


def test_get_default_max_workers_returns_cpu_count() -> None:
    """_get_default_max_workers returns CPU count clamped to 1-32."""
    result = _get_default_max_workers()
    expected = max(1, min(os.cpu_count() or 4, 32))
    assert_that(result).is_equal_to(expected)


def test_get_default_max_workers_handles_none_cpu_count() -> None:
    """_get_default_max_workers handles None cpu_count."""
    with patch.object(os, "cpu_count", return_value=None):
        result = _get_default_max_workers()
        assert_that(result).is_equal_to(4)


def test_get_default_max_workers_clamps_high_count() -> None:
    """_get_default_max_workers clamps high CPU counts to 32."""
    with patch.object(os, "cpu_count", return_value=64):
        result = _get_default_max_workers()
        assert_that(result).is_equal_to(32)


def test_get_default_max_workers_fallback_on_zero() -> None:
    """_get_default_max_workers falls back to 4 when cpu_count returns 0."""
    # 0 is falsy in Python, so `os.cpu_count() or 4` returns 4
    with patch.object(os, "cpu_count", return_value=0):
        result = _get_default_max_workers()
        assert_that(result).is_equal_to(4)


def test_execution_config_precedence_defaults_to_empty() -> None:
    """No configured write precedence means the derived rules decide alone."""
    assert_that(ExecutionConfig().precedence).is_empty()


def test_parse_precedence_lowercases_pairs() -> None:
    """Tool ids are normalised the same way the scheduler normalises them."""
    from lintro.config.config_loader import _parse_precedence

    assert_that(_parse_precedence([["Ruff", "BLACK"]])).is_equal_to(
        [("ruff", "black")],
    )


def test_parse_precedence_rejects_a_malformed_entry() -> None:
    """A precedence entry that is not a pair is a config error, not a guess."""
    from lintro.config.config_loader import _parse_precedence

    assert_that(_parse_precedence).raises(ValueError).when_called_with(
        [["ruff"]],
    )


def test_parse_precedence_rejects_a_self_pair() -> None:
    """A tool cannot take precedence over itself."""
    from lintro.config.config_loader import _parse_precedence

    assert_that(_parse_precedence).raises(ValueError).when_called_with(
        [["ruff", "ruff"]],
    )


@pytest.mark.parametrize(
    "raw",
    [
        "ruff",
        [["ruff"]],
        [["ruff", "black", "typos"]],
        [["ruff", 1]],
        [[None, "black"]],
        [[" ", "black"]],
        [["ruff", "ruff"]],
    ],
    ids=[
        "scalar-not-a-list",
        "one-element-entry",
        "three-element-entry",
        "non-string-part",
        "null-part",
        "blank-name",
        "tool-against-itself",
    ],
)
def test_parse_precedence_rejects_malformed_input(raw: object) -> None:
    """Every rejection path names the key the user has to edit.

    The parser is the only place a precedence pair is validated, so a shape it
    lets through becomes an override that silently matches nothing.

    Args:
        raw: The malformed value as YAML or TOML would produce it.
    """
    from lintro.config.config_loader import _parse_precedence

    assert_that(_parse_precedence).raises(ValueError).when_called_with(raw)
    with pytest.raises(ValueError, match="execution.precedence"):
        _parse_precedence(raw)


@pytest.mark.parametrize(
    "raw",
    [None, [], ()],
    ids=["absent", "empty-list", "empty-tuple"],
)
def test_parse_precedence_accepts_an_empty_override(raw: object) -> None:
    """No configured precedence is not an error; it is the default.

    Args:
        raw: A value meaning "nothing configured".
    """
    from lintro.config.config_loader import _parse_precedence

    assert_that(_parse_precedence(raw)).is_empty()


def test_parse_precedence_keeps_a_repeated_pair() -> None:
    """The parser normalises; it does not de-duplicate.

    Collapsing a pair and its reverse is the scheduler's job, and it raises
    there rather than letting one silently win.
    """
    from lintro.config.config_loader import _parse_precedence

    assert_that(
        _parse_precedence([["ruff", "black"], ["ruff", "black"]]),
    ).is_equal_to([("ruff", "black"), ("ruff", "black")])
