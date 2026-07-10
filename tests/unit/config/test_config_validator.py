"""Tests for the Lintro configuration validator."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_validator import (
    ValidationMessage,
    known_tool_names,
    validate_config_file,
)


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[[str], Path]:
    """Provide a helper that writes a config file and returns its path.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Callable[[str], Path]: Writer that returns the created file path.
    """

    def _write(content: str, name: str = ".lintro-config.yaml") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


def test_known_tool_names_includes_canonical_and_hyphen() -> None:
    """Known tools should include underscore and hyphen forms."""
    names = known_tool_names()

    assert_that(names).contains("ruff")
    assert_that(names).contains("cargo_audit")
    assert_that(names).contains("cargo-audit")
    assert_that(names).contains("markdownlint-cli2")


def test_validation_message_render_with_suggestion() -> None:
    """Render should include location and did-you-mean suggestion."""
    msg = ValidationMessage(
        message="unknown tool 'ruft'",
        location="tools",
        suggestion="ruff",
    )

    rendered = msg.render()

    assert_that(rendered).contains("tools")
    assert_that(rendered).contains("unknown tool 'ruft'")
    assert_that(rendered).contains("did you mean 'ruff'")


def test_valid_config_passes(write_config: Callable[[str], Path]) -> None:
    """A well-formed config should validate cleanly.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
enforce:
  line_length: 88
execution:
  tool_order: priority
tools:
  ruff:
    enabled: true
""",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()
    assert_that(result.errors).is_empty()
    assert_that(result.warnings).is_empty()


def test_missing_file_is_error(tmp_path: Path) -> None:
    """A nonexistent explicit path should produce an error.

    Args:
        tmp_path: Pytest temporary directory.
    """
    result = validate_config_file(tmp_path / "nope.yaml")

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("not found")


def test_no_config_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-detect with no config present should error with a hint.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)

    result = validate_config_file(None)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("lintro init")


def test_unknown_tool_warns_with_suggestion(
    write_config: Callable[[str], Path],
) -> None:
    """An unknown tool name should warn and suggest the closest match.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
tools:
  ruft:
    enabled: true
""",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()
    messages = [w.render() for w in result.warnings]
    assert_that(messages).is_length(1)
    assert_that(messages[0]).contains("unknown tool 'ruft'")
    assert_that(messages[0]).contains("ruff")


def test_unknown_enabled_tool_warns(write_config: Callable[[str], Path]) -> None:
    """Unknown names in execution.enabled_tools should warn.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
execution:
  enabled_tools: [blak]
""",
    )

    result = validate_config_file(path)

    messages = [w.render() for w in result.warnings]
    assert_that(any("unknown tool 'blak'" in m for m in messages)).is_true()
    assert_that(any("black" in m for m in messages)).is_true()


def test_unknown_top_level_key_warns(write_config: Callable[[str], Path]) -> None:
    """Unknown top-level keys should warn.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("bogus_section: 1\n")

    result = validate_config_file(path)

    locations = [w.location for w in result.warnings]
    assert_that(locations).contains("bogus_section")


def test_deprecated_key_warns(write_config: Callable[[str], Path]) -> None:
    """A deprecated key should warn with its replacement.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
enforce:
  line-length: 88
""",
    )

    result = validate_config_file(path)

    dep = [w for w in result.warnings if w.location == "enforce.line-length"]
    assert_that(dep).is_length(1)
    assert_that(dep[0].message).contains("deprecated")
    assert_that(dep[0].suggestion).is_equal_to("line_length")


def test_invalid_value_type_is_error(write_config: Callable[[str], Path]) -> None:
    """A bad execution value type should be a hard error.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
execution:
  max_fix_retries: "not-an-int"
""",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("max_fix_retries")


def test_invalid_auto_install_reports_tool_name(
    write_config: Callable[[str], Path],
) -> None:
    """auto_install type errors should name the offending tool.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
tools:
  ruff:
    auto_install: "yes"
""",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("tools.ruff.auto_install")


def test_non_mapping_root_is_error(write_config: Callable[[str], Path]) -> None:
    """A non-mapping root document should be a hard error.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("- just\n- a\n- list\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("mapping")


def test_empty_config_warns(write_config: Callable[[str], Path]) -> None:
    """An empty config file should warn rather than error.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()
    assert_that(result.warnings[0].message).contains("empty")


def test_malformed_yaml_is_error(write_config: Callable[[str], Path]) -> None:
    """Unparseable YAML should be reported as an error.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("tools:\n  ruff: [unclosed\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("parse")
