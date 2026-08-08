"""Tests for template_aware configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_loader import clear_config_cache, load_config
from lintro.config.lintro_config import LintroConfig
from lintro.config.template_aware_config import (
    StubStrategy,
    TemplateAwareConfig,
    TemplateEngine,
)


def test_template_aware_defaults_disabled() -> None:
    """TemplateAwareConfig is off by default with expected routes."""
    config = TemplateAwareConfig()

    assert_that(config.enabled).is_false()
    assert_that(config.engine).is_equal_to(TemplateEngine.JINJA2)
    assert_that(config.stub_strategy).is_equal_to(StubStrategy.SENTINEL)
    assert_that(config.route).contains_key("*.py.jinja")
    assert_that(config.route["*.py.jinja"]).is_equal_to("ruff")


def test_lintro_config_includes_template_aware_default() -> None:
    """LintroConfig embeds a disabled template_aware section by default."""
    config = LintroConfig()

    assert_that(config.template_aware.enabled).is_false()


def test_load_config_defaults_template_aware_when_section_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing template_aware section stays inert (enabled=false)."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("execution:\n  parallel: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.template_aware.enabled).is_false()
    assert_that(config.template_aware.patterns).is_not_empty()


def test_load_config_parses_template_aware_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML template_aware section is loaded into LintroConfig."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        (
            "template_aware:\n"
            "  enabled: true\n"
            "  engine: copier\n"
            "  stub_strategy: defaults\n"
            "  context_file: .copier-answers.yml\n"
            "  patterns:\n"
            "    - '*.py.jinja'\n"
            "  route:\n"
            "    '*.py.jinja': ruff\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.template_aware.enabled).is_true()
    assert_that(config.template_aware.engine).is_equal_to(TemplateEngine.COPIER)
    assert_that(config.template_aware.stub_strategy).is_equal_to(StubStrategy.DEFAULTS)
    assert_that(config.template_aware.context_file).is_equal_to(".copier-answers.yml")
    assert_that(config.template_aware.patterns).is_equal_to(["*.py.jinja"])
    assert_that(config.template_aware.route["*.py.jinja"]).is_equal_to("ruff")


def test_load_config_parses_context_file_strategy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stub_strategy context_file is accepted."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        (
            "template_aware:\n"
            "  enabled: true\n"
            "  stub_strategy: context_file\n"
            "  context_file: answers.yml\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.template_aware.stub_strategy).is_equal_to(
        StubStrategy.CONTEXT_FILE,
    )
