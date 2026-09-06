"""Tests for the Lintro configuration validator."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_validator import (
    KNOWN_TOP_LEVEL_KEYS,
    ValidationMessage,
    known_tool_names,
    validate_config_file,
)
from lintro.enums.validation_code import ValidationCode


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[..., Path]:
    """Provide a helper that writes a config file and returns its path.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Callable[..., Path]: Writer that returns the created file path.
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
        code=ValidationCode.UNKNOWN_TOOL,
        message="unknown tool 'ruft'",
        location="tools",
        suggestion="ruff",
    )

    rendered = msg.render()

    assert_that(rendered).contains("tools")
    assert_that(rendered).contains("unknown tool 'ruft'")
    assert_that(rendered).contains("did you mean 'ruff'")


def test_valid_config_passes(write_config: Callable[..., Path]) -> None:
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
    write_config: Callable[..., Path],
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


def test_unknown_enabled_tool_warns(write_config: Callable[..., Path]) -> None:
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


def test_unknown_watch_tool_warns(write_config: Callable[..., Path]) -> None:
    """Unknown names in watch.tools should warn with a suggestion.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
watch:
  tools: [ruft]
""",
    )

    result = validate_config_file(path)

    messages = [w.render() for w in result.warnings]
    assert_that(any("unknown tool 'ruft'" in m for m in messages)).is_true()
    assert_that(any("watch.tools" in m and "ruff" in m for m in messages)).is_true()


def test_watch_all_tool_sentinel_is_valid(
    write_config: Callable[..., Path],
) -> None:
    """The watch.tools ``all`` sentinel should not warn.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
watch:
  tools: [all]
""",
    )

    result = validate_config_file(path)

    unknown = [w for w in result.warnings if w.code == ValidationCode.UNKNOWN_TOOL]
    assert_that(unknown).is_empty()


def test_unknown_top_level_key_warns(write_config: Callable[..., Path]) -> None:
    """Unknown top-level keys should warn.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("bogus_section: 1\n")

    result = validate_config_file(path)

    locations = [w.location for w in result.warnings]
    assert_that(locations).contains("bogus_section")


def test_deprecated_key_warns(write_config: Callable[..., Path]) -> None:
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


def test_invalid_value_type_is_error(write_config: Callable[..., Path]) -> None:
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
    write_config: Callable[..., Path],
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


def test_non_mapping_root_is_error(write_config: Callable[..., Path]) -> None:
    """A non-mapping root document should be a hard error.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("- just\n- a\n- list\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("mapping")


def test_empty_config_is_not_valid(write_config: Callable[..., Path]) -> None:
    """An empty config file is not a successful config.

    ``load_config`` ignores empty YAML and continues searching, so validate
    must not report VALID for an explicit empty path.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.EMPTY_CONFIG)
    assert_that(result.errors[0].message).contains("empty")


def test_malformed_yaml_is_error(write_config: Callable[..., Path]) -> None:
    """Unparseable YAML should be reported as an error.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("tools:\n  ruff: [unclosed\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("parse")


def test_pyproject_nested_execution_invalid_max_fix_retries(
    write_config: Callable[..., Path],
) -> None:
    """Invalid nested ``[tool.lintro.execution]`` values must be INVALID.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool.lintro.execution]
max_fix_retries = "not-an-int"
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("max_fix_retries")


def test_pyproject_nested_execution_fail_fast_valid(
    write_config: Callable[..., Path],
) -> None:
    """A valid nested ``[tool.lintro.execution] fail_fast`` must validate.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool.lintro.execution]
fail_fast = true
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()


def test_pyproject_typed_error_is_reported(
    write_config: Callable[..., Path],
) -> None:
    """Typed errors in a pyproject.toml [tool.lintro] table are reported.

    The explicit-path loader reads files as YAML, so validating a
    pyproject.toml must feed the parsed TOML through the shared typed parser
    rather than re-reading the TOML path as YAML.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool.lintro]
max_fix_retries = "not-an-int"
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("max_fix_retries")


def test_pyproject_valid_config_passes(
    write_config: Callable[..., Path],
) -> None:
    """A valid pyproject.toml [tool.lintro] table validates cleanly.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool.lintro]
max_fix_retries = 3
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()


def test_pyproject_unknown_key_warns(write_config: Callable[..., Path]) -> None:
    """Unknown keys on the raw [tool.lintro] table must reach validate output.

    The converter drops unrecognized keys (log only); validate has to inspect
    the raw table or YAML typos in pyproject would look VALID.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool.lintro]
bogus_option = 1
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()
    matches = [w for w in result.warnings if w.location == "tool.lintro.bogus_option"]
    assert_that(matches).is_length(1)
    assert_that(matches[0].code).is_equal_to(ValidationCode.UNKNOWN_OPTION)


def test_pyproject_unknown_tool_table_warns(
    write_config: Callable[..., Path],
) -> None:
    """An unknown tool table under [tool.lintro] should warn UNKNOWN_TOOL.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool.lintro]
[tool.lintro.ruft]
enabled = true
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()
    matches = [w for w in result.warnings if w.code == ValidationCode.UNKNOWN_TOOL]
    assert_that(matches).is_not_empty()
    assert_that(matches[0].message).contains("ruft")


def test_pyproject_non_mapping_tool_table_is_error(
    write_config: Callable[..., Path],
) -> None:
    """A non-mapping ``tool`` value must be INVALID_TYPE, not a crash.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
tool = "not-a-table"
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.INVALID_TYPE)
    assert_that(result.errors[0].location).is_equal_to("tool")


def test_pyproject_non_mapping_lintro_table_is_error(
    write_config: Callable[..., Path],
) -> None:
    """A non-mapping ``tool.lintro`` value must be INVALID_TYPE, not a crash.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool]
lintro = "not-a-table"
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.INVALID_TYPE)
    assert_that(result.errors[0].location).is_equal_to("tool.lintro")


def test_scalar_tool_entry_is_error(write_config: Callable[..., Path]) -> None:
    """A known tool whose value is not a mapping or bool is INVALID_TYPE.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("tools:\n  ruff: 0\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.INVALID_TYPE)
    assert_that(result.errors[0].location).is_equal_to("tools.ruff")
    assert_that(result.errors[0].message).contains("mapping or boolean")


def test_non_string_tool_key_is_error(write_config: Callable[..., Path]) -> None:
    """A numeric ``tools:`` key must be INVALID_TYPE, not a traceback.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("tools:\n  3.14:\n    enabled: true\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.INVALID_TYPE)
    assert_that(result.errors[0].message).contains("tool name must be a string")


def test_pyproject_scalar_execution_is_error(
    write_config: Callable[..., Path],
) -> None:
    """A scalar ``[tool.lintro] execution = false`` entry is INVALID_TYPE.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool.lintro]
execution = false
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.INVALID_TYPE)
    assert_that(result.errors[0].message).contains("execution")
    assert_that(result.errors[0].location).is_equal_to("tool.lintro.execution")


def test_string_tool_entry_is_error(write_config: Callable[..., Path]) -> None:
    """A string tool value such as ``tools.ruff: yes`` is INVALID_TYPE.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config('tools:\n  ruff: "yes"\n')

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.INVALID_TYPE)
    assert_that(result.errors[0].location).is_equal_to("tools.ruff")


def test_pyproject_scalar_tool_entry_is_error(
    write_config: Callable[..., Path],
) -> None:
    """A scalar ``[tool.lintro] ruff = 0`` entry is INVALID_TYPE.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        """
[tool.lintro]
ruff = 0
""",
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.INVALID_TYPE)
    assert_that(result.errors[0].location).is_equal_to("tools.ruff")


@pytest.mark.parametrize(
    "section",
    sorted(KNOWN_TOP_LEVEL_KEYS),
)
def test_documented_sections_do_not_warn(
    write_config: Callable[..., Path],
    section: str,
) -> None:
    """Every known top-level section must validate without UNKNOWN_OPTION.

    Uses an empty mapping so the case does not depend on section-specific
    nested fields.

    Args:
        write_config: Fixture writing config content to a temp file.
        section: Top-level section name to place in the config.
    """
    path = write_config(f"{section}: {{}}\n")

    result = validate_config_file(path)

    unknown = [w for w in result.warnings if w.code == ValidationCode.UNKNOWN_OPTION]
    assert_that(result.is_valid).is_true()
    assert_that(unknown).is_empty()


@pytest.mark.parametrize(
    ("content", "location", "suggestion"),
    [
        ("enforce:\n  line-length: 88\n", "enforce.line-length", "line_length"),
        (
            "enforce:\n  target-python: py311\n",
            "enforce.target-python",
            "target_python",
        ),
        ("global:\n  line_length: 88\n", "global", "enforce"),
    ],
)
def test_deprecated_keys_warn_with_replacement(
    write_config: Callable[..., Path],
    content: str,
    location: str,
    suggestion: str,
) -> None:
    """Each deprecated key should warn and name its modern replacement.

    Args:
        write_config: Fixture writing config content to a temp file.
        content: Config file body containing the deprecated key.
        location: Expected dotted location of the warning.
        suggestion: Expected replacement key name.
    """
    path = write_config(content)

    result = validate_config_file(path)

    matches = [w for w in result.warnings if w.location == location]
    assert_that(matches).is_length(1)
    assert_that(matches[0].code).is_equal_to(ValidationCode.DEPRECATED_OPTION)
    assert_that(matches[0].suggestion).is_equal_to(suggestion)
    # The loader reads only the modern names, so the message must not imply
    # the deprecated spelling still takes effect.
    assert_that(matches[0].message).contains("no longer applied")


@pytest.mark.parametrize("section", ["enforce", "execution", "tools", "defaults"])
def test_null_section_is_error_not_crash(
    write_config: Callable[..., Path],
    section: str,
) -> None:
    """An empty (null) YAML section should report INVALID, not traceback.

    Args:
        write_config: Fixture writing config content to a temp file.
        section: Top-level section spelled with no value.
    """
    path = write_config(f"{section}:\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.INVALID_TYPE)
    assert_that(result.errors[0].location).is_equal_to(section)
    assert_that(result.errors[0].message).contains("mapping")
    assert_that(result.errors[0].message).contains("null")


def test_scalar_section_is_error(write_config: Callable[..., Path]) -> None:
    """A scalar where a mapping is expected should be a hard error.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("tools: 5\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].location).is_equal_to("tools")


def test_empty_mapping_does_not_validate_discovered_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit empty-mapping file must not fall through to auto-discovery.

    ``load_config`` treats a falsy config as "nothing found" and searches
    upward. Validate must still report the requested file as empty (not
    VALID, and not the parent file's typed errors).

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (tmp_path / ".lintro-config.yaml").write_text(
        'execution:\n  max_fix_retries: "bad"\n',
        encoding="utf-8",
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    target = nested / "custom.yaml"
    target.write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(nested)

    result = validate_config_file(target)

    assert_that(result.config_path).is_equal_to(target)
    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.EMPTY_CONFIG)
    messages = [e.message for e in result.errors]
    assert_that(any("max_fix_retries" in m for m in messages)).is_false()


def test_autodetect_falls_back_to_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-detect should validate [tool.lintro] when no YAML config exists.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.lintro]\nmax_fix_retries = "not-an-int"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = validate_config_file(None)

    assert_that(result.config_path).is_equal_to(pyproject)
    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("max_fix_retries")


def test_findings_carry_stable_codes(write_config: Callable[..., Path]) -> None:
    """Findings should expose machine-readable codes for --json consumers.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config("tools:\n  ruft:\n    enabled: true\nbogus: 1\n")

    result = validate_config_file(path)

    codes = {w.code for w in result.warnings}
    assert_that(codes).contains(
        ValidationCode.UNKNOWN_TOOL,
        ValidationCode.UNKNOWN_OPTION,
    )


def test_missing_file_uses_not_found_code(tmp_path: Path) -> None:
    """A nonexistent explicit path should carry the not_found code.

    Args:
        tmp_path: Pytest temporary directory.
    """
    result = validate_config_file(tmp_path / "nope.yaml")

    assert_that(result.errors[0].code).is_equal_to(ValidationCode.NOT_FOUND)


def test_autodetect_list_yaml_falls_through_to_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list YAML on auto-detect must follow the loader into pyproject.

    ``load_config`` maps non-dict YAML to ``{}`` and continues searching.
    Validate must not stop on ``INVALID_TYPE`` and skip ``[tool.lintro]``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (tmp_path / ".lintro-config.yaml").write_text(
        "- just\n- a\n- list\n",
        encoding="utf-8",
    )
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.lintro]\nfail_fast = true\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = validate_config_file(None)

    assert_that(result.config_path).is_equal_to(pyproject)
    assert_that(result.is_valid).is_true()
    assert_that(result.warnings[0].code).is_equal_to(ValidationCode.EMPTY_CONFIG)


def test_autodetect_empty_yaml_reports_invalid_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty auto-detected YAML must not report VALID when pyproject is invalid.

    Runtime ``load_config`` ignores empty YAML and applies [tool.lintro].

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (tmp_path / ".lintro-config.yaml").write_text("", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.lintro]\nmax_fix_retries = "not-an-int"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = validate_config_file(None)

    assert_that(result.config_path).is_equal_to(pyproject)
    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].message).contains("max_fix_retries")
    assert_that(result.warnings[0].code).is_equal_to(ValidationCode.EMPTY_CONFIG)


def test_autodetect_malformed_pyproject_is_parse_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken pyproject.toml on auto-detect is PARSE_ERROR, not NOT_FOUND.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("not: [valid toml\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = validate_config_file(None)

    assert_that(result.config_path).is_equal_to(pyproject)
    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.PARSE_ERROR)


def test_autodetect_empty_yaml_without_pyproject_is_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty YAML with no pyproject fallback is not a successful config.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    yaml_path = tmp_path / ".lintro-config.yaml"
    yaml_path.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = validate_config_file(None)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.NOT_FOUND)
    assert_that(result.warnings[0].code).is_equal_to(ValidationCode.EMPTY_CONFIG)


def test_explicit_pyproject_without_lintro_table_is_empty(
    write_config: Callable[..., Path],
) -> None:
    """An explicit pyproject.toml without [tool.lintro] is EMPTY_CONFIG.

    Args:
        write_config: Fixture writing config content to a temp file.
    """
    path = write_config(
        '[project]\nname = "example"\n',
        name="pyproject.toml",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    assert_that(result.errors[0].code).is_equal_to(ValidationCode.EMPTY_CONFIG)


def test_plugin_tool_name_is_not_unknown(
    write_config: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed plugin names must not produce UNKNOWN_TOOL warnings.

    Args:
        write_config: Fixture writing config content to a temp file.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.config.config_loader.known_plugin_tool_names",
        lambda: frozenset({"acme_lint"}),
    )
    path = write_config(
        """
tools:
  acme_lint:
    enabled: true
""",
    )

    result = validate_config_file(path)

    assert_that(result.is_valid).is_true()
    codes = [w.code for w in result.warnings]
    assert_that(codes).does_not_contain(ValidationCode.UNKNOWN_TOOL)


def test_known_tool_names_includes_plugin_and_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator tool set includes plugin names and the markdownlint alias.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.config.config_loader.known_plugin_tool_names",
        lambda: frozenset({"acme_lint"}),
    )

    names = known_tool_names()

    assert_that(names).contains("acme_lint")
    assert_that(names).contains("acme-lint")
    assert_that(names).contains("markdownlint-cli2")
