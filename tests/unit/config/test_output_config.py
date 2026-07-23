"""Tests for output (console presentation) configuration parsing."""

from __future__ import annotations

from pathlib import Path
from re import escape

import pytest
from assertpy import assert_that

from lintro.config.config_loader import clear_config_cache, load_config


def test_output_art_defaults_to_true_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config without an ``output`` section defaults ``output.art`` to True."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("execution:\n  parallel: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.output.art).is_true()


def test_output_art_can_be_disabled_via_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``output.art: false`` is parsed into the loaded configuration."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("output:\n  art: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.output.art).is_false()


def test_output_art_rejects_non_boolean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-boolean ``output.art`` value raises a clear error."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("output:\n  art: nope\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    with pytest.raises(ValueError, match=escape("output.art must be a boolean")):
        load_config(config_path=config_file)


@pytest.mark.parametrize(
    ("raw_output", "expected_type"),
    [
        ("false", "bool"),
        ("[]", "list"),
        ('""', "str"),
    ],
)
def test_output_rejects_falsey_non_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_output: str,
    expected_type: str,
) -> None:
    """False-y non-mapping ``output`` sections raise the mapping error."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(f"output: {raw_output}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    with pytest.raises(
        ValueError,
        match=escape(f"output config must be a mapping, got {expected_type}"),
    ):
        load_config(config_path=config_file)


def test_output_unknown_keys_are_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown keys under ``output`` are ignored, not fatal."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        "output:\n  art: true\n  bogus: 1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.output.art).is_true()
