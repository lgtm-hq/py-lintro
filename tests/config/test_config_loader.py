"""Tests for config_loader module."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.config.config_loader import (
    MAPPING_SECTIONS,
    _convert_pyproject_to_config,
    _parse_defaults,
    _parse_enforce_config,
    _parse_execution_config,
    _parse_tool_config,
    _parse_tools_config,
    _pyproject_lintro_catalog,
    build_config_from_dict,
    clear_config_cache,
    get_default_config,
    load_config,
)
from lintro.config.enforce_config import EnforceConfig
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig
from lintro.exceptions.errors import ConfigurationError


def test_empty_data() -> None:
    """Should return EnforceConfig with None values."""
    config = _parse_enforce_config({})

    assert_that(config.line_length).is_none()
    assert_that(config.target_python).is_none()


def test_execution_config_empty_data() -> None:
    """Should return execution config defaults."""
    config = _parse_execution_config({})

    assert_that(config.enabled_tools).is_equal_to([])
    assert_that(config.fail_fast).is_false()
    assert_that(config.parallel).is_true()


def test_string_enabled_tools() -> None:
    """Should convert single tool string to list."""
    data = {"enabled_tools": "ruff"}

    config = _parse_execution_config(data)

    assert_that(config.enabled_tools).is_equal_to(["ruff"])


def test_tool_config_empty_data() -> None:
    """Should return default ToolConfig."""
    config = _parse_tool_config("ruff", {})

    assert_that(config.enabled).is_true()
    assert_that(config.config_source).is_none()


def test_disabled_tool() -> None:
    """Should parse enabled=False."""
    data = {"enabled": False}

    config = _parse_tool_config("ruff", data)

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


def test_tools_config_rejects_non_string_key() -> None:
    """A numeric ``tools:`` key must raise ValueError, not AttributeError."""
    with pytest.raises(ValueError, match="tool name must be a string"):
        _parse_tools_config(
            cast(dict[str, Any], {3.14: {"enabled": True}}),
        )


def test_load_config_non_string_tool_key_raises_configuration_error(
    tmp_path: Path,
) -> None:
    """YAML ``tools.3.14`` must exit as ConfigurationError, not a traceback.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("tools:\n  3.14:\n    enabled: true\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="tool name must be a string"):
        load_config(config_path=str(config_file))


def test_tools_config_rejects_scalar_entry() -> None:
    """A known tool whose value is not a mapping or bool must raise.

    ``tools.ruff: 0`` used to be dropped, so ruff stayed at the default
    (enabled). The parser must fail closed instead.
    """
    with pytest.raises(ValueError) as exc_info:
        _parse_tools_config({"ruff": 0})

    assert_that(str(exc_info.value)).contains("tools.ruff")
    assert_that(str(exc_info.value)).contains("mapping or boolean")


def test_tools_config_rejects_string_entry() -> None:
    """A string tool value such as ``"yes"`` must raise."""
    with pytest.raises(ValueError) as exc_info:
        _parse_tools_config({"ruff": "yes"})

    assert_that(str(exc_info.value)).contains("tools.ruff")


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
        "fail_fast": True,
        "parallel": False,
    }

    result = _convert_pyproject_to_config(data)

    assert_that(result["execution"]["fail_fast"]).is_true()
    assert_that(result["execution"]["parallel"]).is_false()


def test_convert_nested_execution_and_enforce_tables() -> None:
    """Nested ``[tool.lintro.execution]`` / ``enforce`` tables must be merged."""
    result = _convert_pyproject_to_config(
        {
            "execution": {"fail_fast": True, "max_fix_retries": 5},
            "enforce": {"line_length": 100},
        },
    )

    assert_that(result["execution"]["fail_fast"]).is_true()
    assert_that(result["execution"]["max_fix_retries"]).is_equal_to(5)
    assert_that(result["enforce"]["line_length"]).is_equal_to(100)


def test_pyproject_catalog_keys_match_model_fields() -> None:
    """Catalog execution/enforce keys must come from the Pydantic models."""
    catalog = _pyproject_lintro_catalog()

    assert_that(catalog.execution_keys).is_equal_to(
        frozenset(ExecutionConfig.model_fields),
    )
    assert_that(catalog.enforce_keys).is_equal_to(
        frozenset(EnforceConfig.model_fields),
    )


def test_parse_tools_config_rejects_null_entry() -> None:
    """A null ``tools.<name>`` value must raise ValueError."""
    with pytest.raises(ValueError, match="tools.ruff must be a mapping or boolean"):
        _parse_tools_config({"ruff": None})


@pytest.mark.parametrize("section", ["execution", "enforce"])
@pytest.mark.parametrize("value", [None, False, [], "", 0])
def test_convert_non_mapping_execution_enforce_raises(
    section: str,
    value: Any,
) -> None:
    """Reject invalid tables, including null which TOML cannot represent.

    Args:
        section: Nested pyproject section that requires a mapping.
        value: Invalid section value supplied directly to the converter.
    """
    with pytest.raises(ValueError) as error:
        _convert_pyproject_to_config({section: value})

    actual = "null" if value is None else type(value).__name__
    assert_that(str(error.value)).is_equal_to(
        f"tool.lintro.{section} must be a mapping, got {actual}",
    )


@pytest.mark.parametrize(
    "section",
    MAPPING_SECTIONS,
)
def test_build_config_from_dict_rejects_null_mapping_section(
    section: str,
) -> None:
    """A YAML-null mapping section must raise ValueError, not AttributeError.

    Args:
        section: Top-level key that typed parsers assume is a mapping.
    """
    with pytest.raises(ValueError, match=f"{section} must be a mapping"):
        build_config_from_dict({section: None})


@pytest.mark.parametrize(
    "section",
    MAPPING_SECTIONS,
)
@pytest.mark.parametrize(
    ("raw_value", "actual"),
    [
        ("", "null"),
        ("null", "null"),
        ("~", "null"),
        ("false", "bool"),
        ("[]", "list"),
        ('""', "str"),
        ("0", "int"),
        ("invalid", "str"),
    ],
)
def test_load_config_rejects_non_mapping_sections(
    tmp_path: Path,
    section: str,
    raw_value: str,
    actual: str,
) -> None:
    """Explicit null and non-mapping sections must raise ConfigurationError.

    Args:
        tmp_path: Temporary directory path for test files.
        section: Top-level section that must be a mapping.
        raw_value: YAML spelling of an invalid section value.
        actual: Type named in the error.
    """
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(f"{section}: {raw_value}\n", encoding="utf-8")

    assert_that(load_config).raises(ConfigurationError).when_called_with(
        config_path=config_file,
        allow_pyproject_fallback=False,
    ).contains(f"{section} must be a mapping, got {actual}")


@pytest.mark.parametrize("section", MAPPING_SECTIONS)
@pytest.mark.parametrize("raw_value", ["false", "[]", '""', "0"])
def test_pyproject_rejects_non_mapping_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    raw_value: str,
) -> None:
    """Pyproject conversion must preserve invalid sections for validation.

    Args:
        tmp_path: Temporary directory path for test files.
        monkeypatch: Pytest monkeypatch fixture.
        section: Top-level section that must be a mapping.
        raw_value: TOML spelling of an invalid section value.
    """
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.lintro]\n{section} = {raw_value}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert_that(load_config).raises(ConfigurationError).when_called_with().contains(
        f"{section} must be a mapping",
    )


@pytest.mark.parametrize("table", ["tool", "tools", "execution", "enforce"])
@pytest.mark.parametrize(
    ("raw_value", "actual"),
    [("false", "bool"), ("[]", "list"), ('""', "str"), ("0", "int")],
)
def test_pyproject_table_error_names_full_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    table: str,
    raw_value: str,
    actual: str,
) -> None:
    """Invalid nested tables identify the full pyproject key in the error.

    Args:
        tmp_path: Temporary directory path for test files.
        monkeypatch: Pytest monkeypatch fixture.
        table: Nested table that must contain a mapping.
        raw_value: TOML spelling of the invalid value.
        actual: Type reported in the error.
    """
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.lintro]\n{table} = {raw_value}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigurationError) as error:
        load_config()

    assert_that(str(error.value)).is_equal_to(
        f"tool.lintro.{table} must be a mapping, got {actual}",
    )


@pytest.mark.parametrize(
    ("settings", "expected_execution", "expected_enforce"),
    [
        pytest.param("", {}, {}, id="omitted"),
        pytest.param("execution = {}\nenforce = {}\n", {}, {}, id="empty"),
        pytest.param(
            "fail_fast = true\nline_length = 88\n"
            "execution = { parallel = false }\n"
            'enforce = { target_python = "py313" }\n',
            {"fail_fast": True, "parallel": False},
            {"line_length": 88, "target_python": "py313"},
            id="nested-and-flat",
        ),
    ],
)
def test_load_pyproject_execution_enforce_mappings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: str,
    expected_execution: dict[str, Any],
    expected_enforce: dict[str, Any],
) -> None:
    """Load defaults and merge valid nested tables with flat settings.

    Args:
        tmp_path: Temporary directory for the pyproject file.
        monkeypatch: Pytest monkeypatch fixture.
        settings: Omitted, empty or populated execution/enforce tables.
        expected_execution: Execution values overriding model defaults.
        expected_enforce: Enforce values overriding model defaults.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        f"[tool.lintro]\noutput = {{ art = false }}\n{settings}",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert_that(config.config_path).is_equal_to(str(pyproject.resolve()))
    assert_that(config.execution).is_equal_to(ExecutionConfig(**expected_execution))
    assert_that(config.enforce).is_equal_to(EnforceConfig(**expected_enforce))


@pytest.mark.parametrize("explicit_sections", [False, True])
def test_omitted_and_empty_mapping_sections_use_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_sections: bool,
) -> None:
    """Omitted sections and explicit empty mappings keep all model defaults."""
    config_file = tmp_path / ".lintro-config.yaml"
    content = (
        "\n".join(f"{section}: {{}}" for section in MAPPING_SECTIONS)
        if explicit_sections
        else "{}"
    )
    config_file.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = load_config(config_path=config_file, allow_pyproject_fallback=False)

    assert_that(config.model_dump(exclude={"config_path"})).is_equal_to(
        LintroConfig().model_dump(exclude={"config_path"}),
    )


@pytest.mark.parametrize("explicit_path", [False, True])
@pytest.mark.parametrize(
    "content",
    ["[]\n", "- ruff\n", "invalid\n", "false\n", "0\n", "null\n", "", "# comment\n"],
)
def test_non_mapping_document_does_not_fall_back_to_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_path: bool,
    content: str,
) -> None:
    """Invalid YAML roots must fail even when a valid fallback config exists."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(content, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.lintro]\nfail_fast = true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert_that(load_config).raises(ConfigurationError).when_called_with(
        config_path=config_file if explicit_path else None,
    ).contains("must be a mapping").contains(str(config_file))


@pytest.mark.parametrize("fallback_source", ["search", "pyproject"])
def test_empty_mapping_document_preserves_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback_source: str,
) -> None:
    """An explicit empty mapping still searches YAML before pyproject fallback.

    Args:
        tmp_path: Temporary directory path for test files.
        monkeypatch: Pytest monkeypatch fixture.
        fallback_source: Source that supplies the effective configuration.
    """
    empty_file = tmp_path / "empty.yaml"
    empty_file.write_text("{}\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.lintro]\nfail_fast = true\n", encoding="utf-8")
    expected_path = pyproject
    if fallback_source == "search":
        expected_path = tmp_path / ".lintro-config.yaml"
        expected_path.write_text("execution: {fail_fast: false}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = load_config(config_path=empty_file)

    assert_that(config.execution.fail_fast).is_equal_to(fallback_source == "pyproject")
    assert_that(config.config_path).is_equal_to(str(expected_path.resolve()))


def test_load_config_null_tool_raises_configuration_error(tmp_path: Path) -> None:
    """Runtime load must wrap a null tool entry as ConfigurationError.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("tools:\n  ruff:\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="tools.ruff must be a mapping"):
        load_config(config_path=str(config_file))


def test_load_yaml_config_with_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should load .lintro-config.yaml file with defaults section.

    Args:
        tmp_path: Temporary directory path for test files.
        monkeypatch: Pytest monkeypatch fixture.
    """
    config_content = """\
defaults:
  prettier:
    singleQuote: true
    tabWidth: 2
"""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(config_content)

    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config()

    assert_that(config.get_tool_defaults("prettier")["singleQuote"]).is_true()
    assert_that(config.get_tool_defaults("prettier")["tabWidth"]).is_equal_to(2)


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


def test_returns_default_when_no_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should return default config when no file found.

    Args:
        tmp_path: Temporary directory path for test files.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(allow_pyproject_fallback=False)

    # Should get default empty config
    assert_that(config.enforce.line_length).is_none()


def test_returns_sensible_defaults() -> None:
    """Should return config with sensible defaults."""
    config = get_default_config()

    assert_that(config.enforce.line_length).is_equal_to(88)
    # target_python is None to let tools infer from requires-python
    assert_that(config.enforce.target_python).is_none()


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
        "lintro.utils.plugin_tool_names.advertised_plugin_tool_names",
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
                "versions": {},
                "module_size": {},
                "plugins": {"enabled": True},
            },
        ),
    )

    assert_that(output).is_empty()


def test_externally_parsed_keys_do_not_warn() -> None:
    """Documented keys parsed by other loaders must not trip the warning.

    ``licenses`` is read by ``lintro.config.licenses_config``, ``module_size``
    by ``lintro.utils.config`` and ``plugins`` by ``lintro.plugins.discovery``;
    none of them reach this converter, so warning about them would cry wolf on
    valid config.
    """
    output = _capture_warnings(
        lambda: _convert_pyproject_to_config(
            {
                "licenses": {"allow": ["MIT"]},
                "module_size": {"threshold": 800},
                "plugins": {"enabled": True},
            },
        ),
    )

    assert_that(output).is_empty()


def test_plugin_cannot_shadow_reserved_config_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry point named like a config key must not hijack that key.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _fake_plugin_names(monkeypatch, "fail-fast", "line-length")

    result = _convert_pyproject_to_config({"fail_fast": True, "line_length": 100})

    assert_that(result["execution"]["fail_fast"]).is_true()
    assert_that(result["enforce"]["line_length"]).is_equal_to(100)
    assert_that(result["tools"]).is_empty()


def test_nested_pyproject_execution_fail_fast_is_applied() -> None:
    """Valid nested ``[tool.lintro.execution] fail_fast`` must reach LintroConfig."""
    import tomllib

    parsed = tomllib.loads("[tool.lintro.execution]\nfail_fast = true\n")
    converted = _convert_pyproject_to_config(parsed["tool"]["lintro"])
    config = build_config_from_dict(converted)

    assert_that(config.execution.fail_fast).is_true()
