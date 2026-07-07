"""Tests for watch-mode configuration parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_loader import _parse_watch_config, load_config
from lintro.config.watch_config import WatchConfig


def test_watch_config_defaults() -> None:
    """An unset watch config uses sensible defaults."""
    cfg = WatchConfig()

    assert_that(cfg.debounce_ms).is_equal_to(300)
    assert_that(cfg.auto_fix).is_false()
    assert_that(cfg.clear_screen).is_false()
    assert_that(cfg.tools).is_empty()
    assert_that(cfg.ignore).is_empty()


def test_watch_config_rejects_negative_debounce() -> None:
    """A negative debounce interval is rejected."""
    assert_that(WatchConfig).raises(ValueError).when_called_with(debounce_ms=-5)


def test_parse_watch_config_from_mapping() -> None:
    """A populated mapping is parsed into a WatchConfig."""
    cfg = _parse_watch_config(
        {
            "debounce_ms": 500,
            "auto_fix": True,
            "clear_screen": True,
            "tools": ["ruff", "mypy"],
            "ignore": ["**/build/**"],
        },
    )

    assert_that(cfg.debounce_ms).is_equal_to(500)
    assert_that(cfg.auto_fix).is_true()
    assert_that(cfg.clear_screen).is_true()
    assert_that(cfg.tools).is_equal_to(["ruff", "mypy"])
    assert_that(cfg.ignore).is_equal_to(["**/build/**"])


def test_parse_watch_config_empty_returns_defaults() -> None:
    """An empty or None section yields default config."""
    assert_that(_parse_watch_config({}).debounce_ms).is_equal_to(300)
    assert_that(_parse_watch_config(None).debounce_ms).is_equal_to(300)


def test_parse_watch_config_rejects_non_mapping() -> None:
    """A non-mapping watch section raises ValueError."""
    assert_that(_parse_watch_config).raises(ValueError).when_called_with(
        ["not", "a", "mapping"],
    )


def test_parse_watch_config_ignores_unknown_keys() -> None:
    """Unknown keys are dropped rather than raising."""
    cfg = _parse_watch_config({"debounce_ms": 200, "bogus": True})

    assert_that(cfg.debounce_ms).is_equal_to(200)


def test_load_config_reads_watch_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_config surfaces a watch section from .lintro-config.yaml."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        "watch:\n"
        "  debounce_ms: 750\n"
        "  auto_fix: true\n"
        "  tools: [ruff]\n",
    )
    monkeypatch.chdir(tmp_path)

    cfg = load_config(config_path=config_file)

    assert_that(cfg.watch.debounce_ms).is_equal_to(750)
    assert_that(cfg.watch.auto_fix).is_true()
    assert_that(cfg.watch.tools).is_equal_to(["ruff"])
