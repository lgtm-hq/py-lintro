"""Tests for config_loader module."""

from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.config.config_loader import (
    _convert_pyproject_to_config,
    _parse_defaults,
    _parse_enforce_config,
    _parse_execution_config,
    _parse_tool_config,
    _parse_tools_config,
    clear_config_cache,
    get_default_config,
    load_config,
)
from lintro.plugins import discovery


def test_empty_data() -> None:
    """Should return EnforceConfig with None values."""
    config = _parse_enforce_config({})

    assert_that(config.line_length).is_none()
    assert_that(config.target_python).is_none()


def test_execution_config_empty_data() -> None:
    """Should return execution config defaults."""
    config = _parse_execution_config({})

    assert_that(config.enabled_tools).is_equal_to([])
    assert_that(config.tool_order).is_equal_to("priority")
    assert_that(config.fail_fast).is_false()
    assert_that(config.parallel).is_true()


def test_string_enabled_tools() -> None:
    """Should convert single tool string to list."""
    data = {"enabled_tools": "ruff"}

    config = _parse_execution_config(data)

    assert_that(config.enabled_tools).is_equal_to(["ruff"])


def test_tool_config_empty_data() -> None:
    """Should return default ToolConfig."""
    config = _parse_tool_config({})

    assert_that(config.enabled).is_true()
    assert_that(config.config_source).is_none()


def test_disabled_tool() -> None:
    """Should parse enabled=False."""
    data = {"enabled": False}

    config = _parse_tool_config(data)

    assert_that(config.enabled).is_false()


def test_tools_config_empty_data() -> None:
    """Should return empty dict for tools config."""
    config = _parse_tools_config({})

    assert_that(config).is_equal_to({})


def test_with_bool_values() -> None:
    """Should handle bool as enabled flag."""
    data = {
        "ruff": True,
        "prettier": False,
    }

    config = _parse_tools_config(data)

    assert_that(config["ruff"].enabled).is_true()
    assert_that(config["prettier"].enabled).is_false()


def test_defaults_empty_data() -> None:
    """Should return empty dict for defaults."""
    defaults = _parse_defaults({})

    assert_that(defaults).is_equal_to({})


def test_case_normalization() -> None:
    """Tool names should be lowercased."""
    data = {"PRETTIER": {"singleQuote": True}}

    defaults = _parse_defaults(data)

    assert_that(defaults).contains("prettier")
    assert_that(defaults).does_not_contain("PRETTIER")


def test_convert_pyproject_empty_data() -> None:
    """Should return structure with empty sections."""
    result = _convert_pyproject_to_config({})

    assert_that(result).contains("enforce")
    assert_that(result).contains("execution")
    assert_that(result).contains("defaults")
    assert_that(result).contains("tools")


def test_enforce_settings() -> None:
    """Should extract enforce settings."""
    data = {
        "line_length": 88,
        "target_python": "py313",
    }

    result = _convert_pyproject_to_config(data)

    assert_that(result["enforce"]["line_length"]).is_equal_to(88)
    assert_that(result["enforce"]["target_python"]).is_equal_to("py313")


def test_tool_sections() -> None:
    """Should extract tool-specific sections."""
    data = {
        "ruff": {"enabled": True},
        "markdownlint": {"enabled": False},
    }

    result = _convert_pyproject_to_config(data)

    assert_that(result["tools"]["ruff"]).is_equal_to({"enabled": True})
    assert_that(result["tools"]["markdownlint"]).is_equal_to({"enabled": False})


def test_execution_settings() -> None:
    """Should extract execution settings."""
    data = {
        "tool_order": "alphabetical",
        "fail_fast": True,
    }

    result = _convert_pyproject_to_config(data)

    assert_that(result["execution"]["tool_order"]).is_equal_to("alphabetical")
    assert_that(result["execution"]["fail_fast"]).is_true()


def test_load_yaml_config_with_defaults(tmp_path: Path) -> None:
    """Should load .lintro-config.yaml file with defaults section.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    config_content = """\
defaults:
  prettier:
    singleQuote: true
    tabWidth: 2
"""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(config_content)

    import os

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        clear_config_cache()

        config = load_config()

        assert_that(config.get_tool_defaults("prettier")["singleQuote"]).is_true()
        assert_that(config.get_tool_defaults("prettier")["tabWidth"]).is_equal_to(2)
    finally:
        os.chdir(original_cwd)


def test_load_explicit_path(tmp_path: Path) -> None:
    """Should load from explicit path.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    config_content = """\
enforce:
  line_length: 120
"""
    config_file = tmp_path / "custom-config.yaml"
    config_file.write_text(config_content)

    config = load_config(config_path=str(config_file))

    assert_that(config.enforce.line_length).is_equal_to(120)


def test_score_config_defaults(tmp_path: Path) -> None:
    """Score config falls back to defaults when unspecified.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    config_file = tmp_path / "custom-config.yaml"
    config_file.write_text("enforce:\n  line_length: 88\n")

    config = load_config(config_path=str(config_file))

    assert_that(config.score.error_weight).is_equal_to(10.0)
    assert_that(config.score.warning_weight).is_equal_to(3.0)
    assert_that(config.score.info_weight).is_equal_to(1.0)
    assert_that(config.score.scale).is_equal_to(100.0)


def test_score_config_from_yaml(tmp_path: Path) -> None:
    """Score weights and scale load from the ``score`` section.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    config_content = """\
score:
  error_weight: 20
  warning_weight: 5
  info_weight: 2
  scale: 200
"""
    config_file = tmp_path / "custom-config.yaml"
    config_file.write_text(config_content)

    config = load_config(config_path=str(config_file))

    assert_that(config.score.error_weight).is_equal_to(20.0)
    assert_that(config.score.warning_weight).is_equal_to(5.0)
    assert_that(config.score.info_weight).is_equal_to(2.0)
    assert_that(config.score.scale).is_equal_to(200.0)


def test_returns_default_when_no_config(tmp_path: Path) -> None:
    """Should return default config when no file found.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    import os

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        clear_config_cache()

        config = load_config(allow_pyproject_fallback=False)

        # Should get default empty config
        assert_that(config.enforce.line_length).is_none()
    finally:
        os.chdir(original_cwd)


def test_returns_sensible_defaults() -> None:
    """Should return config with sensible defaults."""
    config = get_default_config()

    assert_that(config.enforce.line_length).is_equal_to(88)
    # target_python is None to let tools infer from requires-python
    assert_that(config.enforce.target_python).is_none()
    assert_that(config.execution.tool_order).is_equal_to("priority")


def _fake_plugin_names(
    monkeypatch: pytest.MonkeyPatch,
    *names: str,
) -> None:
    """Pretend the given tool names are advertised by installed plugins.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        *names: Entry-point tool names to advertise.
    """
    monkeypatch.setattr(
        discovery,
        "_advertised_plugin_tool_names",
        lambda: frozenset(names),
    )


def _capture_warnings(callback: Callable[[], object]) -> str:
    """Run a callback and return the WARNING-level loguru output it emitted.

    Args:
        callback: Zero-argument callable to invoke while capturing. Any return
            value is discarded.

    Returns:
        str: Concatenated warning records emitted during the call.
    """
    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="WARNING",
    )
    try:
        callback()
    finally:
        logger.remove(handler_id)
    return "".join(messages)


def test_plugin_tool_section_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A top-level section for an installed plugin tool must survive (#1757).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _fake_plugin_names(monkeypatch, "my-plugin")

    result = _convert_pyproject_to_config({"my_plugin": {"enabled": False}})

    assert_that(result["tools"]).contains_key("my-plugin")
    assert_that(result["tools"]["my-plugin"]).is_equal_to({"enabled": False})


def test_nested_plugin_tool_section_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nested ``[tool.lintro.tool.<plugin>]`` section must survive (#1757).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _fake_plugin_names(monkeypatch, "my-plugin")

    result = _convert_pyproject_to_config(
        {"tool": {"my_plugin": {"enabled": False}}},
    )

    assert_that(result["tools"]).contains_key("my-plugin")
    assert_that(result["tools"]["my-plugin"]).is_equal_to({"enabled": False})


def test_unknown_plugin_tool_section_is_still_dropped() -> None:
    """A section for a tool nobody advertises is not silently promoted."""
    result = _convert_pyproject_to_config({"my_plugin": {"enabled": False}})

    assert_that(result["tools"]).does_not_contain_key("my-plugin")
    assert_that(result["tools"]).does_not_contain_key("my_plugin")


def test_unknown_key_warns() -> None:
    """An unrecognized top-level key warns instead of vanishing."""
    output = _capture_warnings(
        lambda: _convert_pyproject_to_config({"totally_bogus_key": 1}),
    )

    assert_that(output).contains("totally_bogus_key")
    assert_that(output).contains("Ignoring unrecognized")


def test_unknown_nested_tool_key_warns() -> None:
    """An unrecognized nested tool name warns instead of vanishing."""
    output = _capture_warnings(
        lambda: _convert_pyproject_to_config({"tool": {"nope": {"enabled": False}}}),
    )

    assert_that(output).contains("tool.nope")


def test_known_keys_do_not_warn() -> None:
    """Recognized tools, sections and externally parsed tables stay quiet."""
    output = _capture_warnings(
        lambda: _convert_pyproject_to_config(
            {
                "ruff": {"enabled": True},
                "line_length": 88,
                "tool_order": "priority",
                "post_checks": {},
                "versions": {},
                "module_size": {},
                "plugins": {"enabled": True},
            },
        ),
    )

    assert_that(output).is_empty()
