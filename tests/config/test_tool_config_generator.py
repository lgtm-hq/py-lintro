"""Tests for tool_config_generator module."""

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.enforce_config import EnforceConfig
from lintro.config.lintro_config import LintroConfig
from lintro.config.tool_config_generator import (
    NATIVE_KEY_MAPPINGS,
    _convert_python_version_for_mypy,
    _transform_keys_for_native_config,
    _write_defaults_config,
    generate_defaults_config,
    get_defaults_injection_args,
    get_enforce_cli_args,
    has_native_config,
)
from lintro.enums.config_format import ConfigFormat


def test_returns_empty_when_no_enforce_settings() -> None:
    """Should return empty list when no enforce settings."""
    lintro_config = LintroConfig()

    args = get_enforce_cli_args(
        tool_name="ruff",
        lintro_config=lintro_config,
    )

    assert_that(args).is_empty()


def test_injects_line_length_for_black() -> None:
    """Should inject --line-length for black."""
    lintro_config = LintroConfig(
        enforce=EnforceConfig(line_length=88),
    )

    args = get_enforce_cli_args(
        tool_name="black",
        lintro_config=lintro_config,
    )

    assert_that(args).is_equal_to(["--line-length", "88"])


def test_injects_target_version_for_ruff() -> None:
    """Should inject --target-version for ruff."""
    lintro_config = LintroConfig(
        enforce=EnforceConfig(target_python="py312"),
    )

    args = get_enforce_cli_args(
        tool_name="ruff",
        lintro_config=lintro_config,
    )

    assert_that(args).is_equal_to(["--target-version", "py312"])


def test_injects_both_line_length_and_target_version() -> None:
    """Should inject both settings when both are set."""
    lintro_config = LintroConfig(
        enforce=EnforceConfig(
            line_length=100,
            target_python="py313",
        ),
    )

    args = get_enforce_cli_args(
        tool_name="ruff",
        lintro_config=lintro_config,
    )

    assert_that(args).contains("--line-length")
    assert_that(args).contains("100")
    assert_that(args).contains("--target-version")
    assert_that(args).contains("py313")


def test_converts_target_version_format_for_mypy() -> None:
    """Should convert py313 format to 3.13 for mypy."""
    lintro_config = LintroConfig(
        enforce=EnforceConfig(target_python="py313"),
    )

    args = get_enforce_cli_args(
        tool_name="mypy",
        lintro_config=lintro_config,
    )

    assert_that(args).is_equal_to(["--python-version", "3.13"])


def test_convert_python_version_helper_handles_plain_version() -> None:
    """Should return plain version unchanged when already numeric."""
    assert_that(_convert_python_version_for_mypy("3.12")).is_equal_to("3.12")


def test_returns_empty_for_unsupported_tool() -> None:
    """Should return empty list for tools without CLI mappings."""
    lintro_config = LintroConfig(
        enforce=EnforceConfig(line_length=100),
    )

    args = get_enforce_cli_args(
        tool_name="yamllint",
        lintro_config=lintro_config,
    )

    # yamllint doesn't support --line-length CLI flag
    assert_that(args).is_empty()


def test_returns_empty_for_none_path() -> None:
    """Should return empty list when no config path."""
    args = get_defaults_injection_args(
        tool_name="prettier",
        config_path=None,
    )

    assert_that(args).is_empty()


def test_markdownlint_config_uses_correct_suffix() -> None:
    """Should use .markdownlint-cli2.jsonc suffix for markdownlint.

    markdownlint-cli2 v0.17+ requires config files to follow strict naming
    conventions. The temp config file must end with a recognized suffix.
    """
    config_path = _write_defaults_config(
        defaults={"config": {"MD013": {"line_length": 100}}},
        tool_name="markdownlint",
        config_format=ConfigFormat.JSON,
    )

    try:
        assert_that(str(config_path)).ends_with(".markdownlint-cli2.jsonc")
        assert_that(config_path.exists()).is_true()
    finally:
        config_path.unlink(missing_ok=True)


def test_generic_tool_config_uses_json_suffix() -> None:
    """Should use .json suffix for tools without special requirements."""
    config_path = _write_defaults_config(
        defaults={"some": "config"},
        tool_name="prettier",
        config_format=ConfigFormat.JSON,
    )

    try:
        assert_that(str(config_path)).ends_with(".json")
        assert_that(config_path.exists()).is_true()
    finally:
        config_path.unlink(missing_ok=True)


# =============================================================================
# Key transformation tests
# =============================================================================


def test_hadolint_key_mapping_exists() -> None:
    """Should have key mappings defined for hadolint."""
    assert_that(NATIVE_KEY_MAPPINGS).contains_key("hadolint")
    hadolint_mappings = NATIVE_KEY_MAPPINGS["hadolint"]
    assert_that(hadolint_mappings).contains_key("trusted_registries")
    assert_that(hadolint_mappings["trusted_registries"]).is_equal_to(
        "trustedRegistries",
    )


def test_transform_keys_converts_hadolint_trusted_registries() -> None:
    """Should convert trusted_registries to trustedRegistries for hadolint."""
    defaults = {
        "ignored": ["DL3006"],
        "trusted_registries": ["docker.io", "gcr.io"],
    }

    transformed = _transform_keys_for_native_config(defaults, "hadolint")

    assert_that(transformed).contains_key("trustedRegistries")
    assert_that(transformed).does_not_contain_key("trusted_registries")
    assert_that(transformed["trustedRegistries"]).is_equal_to(["docker.io", "gcr.io"])
    # "ignored" should remain unchanged
    assert_that(transformed).contains_key("ignored")
    assert_that(transformed["ignored"]).is_equal_to(["DL3006"])


def test_transform_keys_preserves_unmapped_keys() -> None:
    """Should preserve keys that have no mapping."""
    defaults = {
        "ignored": ["DL3006"],
        "custom_key": "value",
    }

    transformed = _transform_keys_for_native_config(defaults, "hadolint")

    assert_that(transformed).contains_key("ignored")
    assert_that(transformed).contains_key("custom_key")


def test_transform_keys_returns_unchanged_for_unknown_tool() -> None:
    """Should return defaults unchanged for tools without mappings."""
    defaults = {
        "some_key": "value",
        "another_key": 123,
    }

    transformed = _transform_keys_for_native_config(defaults, "prettier")

    assert_that(transformed).is_equal_to(defaults)


def test_hadolint_config_file_has_correct_keys() -> None:
    """Should write hadolint config with camelCase keys."""
    import yaml

    defaults = {
        "ignored": [],
        "trusted_registries": ["docker.io", "gcr.io"],
    }

    config_path = _write_defaults_config(
        defaults=defaults,
        tool_name="hadolint",
        config_format=ConfigFormat.YAML,
    )

    try:
        content = config_path.read_text()
        parsed = yaml.safe_load(content)

        # Should have camelCase key
        assert_that(parsed).contains_key("trustedRegistries")
        assert_that(parsed).does_not_contain_key("trusted_registries")
        assert_that(parsed["trustedRegistries"]).is_equal_to(["docker.io", "gcr.io"])
        # ignored should remain as-is
        assert_that(parsed).contains_key("ignored")
    finally:
        config_path.unlink(missing_ok=True)


# =============================================================================
# Oxlint and Oxfmt config tests
# =============================================================================


def test_get_defaults_injection_args_oxlint(tmp_path: Path) -> None:
    """Should return correct config args for oxlint.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    config_path = tmp_path / "test.json"
    config_path.write_text("{}")
    args = get_defaults_injection_args("oxlint", config_path)

    assert_that(args).is_equal_to(["--config", str(config_path)])


def test_get_defaults_injection_args_oxfmt(tmp_path: Path) -> None:
    """Should return correct config args for oxfmt.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    config_path = tmp_path / "test.json"
    config_path.write_text("{}")
    args = get_defaults_injection_args("oxfmt", config_path)

    assert_that(args).is_equal_to(["--config", str(config_path)])


def test_has_native_config_oxlint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should detect oxlint native config file.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".oxlintrc.json").write_text('{"rules": {}}')

    assert_that(has_native_config("oxlint")).is_true()


def test_has_native_config_oxfmt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should detect oxfmt native config file.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".oxfmtrc.json").write_text('{"printWidth": 100}')

    assert_that(has_native_config("oxfmt")).is_true()


def test_has_native_config_oxlint_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should return False when no oxlint config exists.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)

    assert_that(has_native_config("oxlint")).is_false()


def test_has_native_config_oxfmt_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should return False when no oxfmt config exists.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)

    assert_that(has_native_config("oxfmt")).is_false()


# =============================================================================
# Prettier builtin defaults tests
# =============================================================================


def test_prettier_builtin_defaults_applied_when_no_user_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should generate config with builtin defaults when user sets none.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    lintro_config = LintroConfig()

    config_path = generate_defaults_config("prettier", lintro_config)

    assert_that(config_path).is_not_none()
    assert config_path is not None
    import json

    content = json.loads(config_path.read_text())
    assert_that(content).contains_key("proseWrap")
    assert_that(content["proseWrap"]).is_equal_to("always")
    config_path.unlink(missing_ok=True)


def test_prettier_user_defaults_override_builtin_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should let user defaults override builtin defaults.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    lintro_config = LintroConfig(
        defaults={"prettier": {"proseWrap": "never"}},
    )

    config_path = generate_defaults_config("prettier", lintro_config)

    assert_that(config_path).is_not_none()
    assert config_path is not None
    import json

    content = json.loads(config_path.read_text())
    assert_that(content["proseWrap"]).is_equal_to("never")
    config_path.unlink(missing_ok=True)


def test_prettier_user_defaults_merged_with_builtin_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should merge user defaults on top of builtin defaults.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    lintro_config = LintroConfig(
        defaults={"prettier": {"tabWidth": 4}},
    )

    config_path = generate_defaults_config("prettier", lintro_config)

    assert_that(config_path).is_not_none()
    assert config_path is not None
    import json

    content = json.loads(config_path.read_text())
    # Builtin default should be present
    assert_that(content["proseWrap"]).is_equal_to("always")
    # User default should also be present
    assert_that(content["tabWidth"]).is_equal_to(4)
    config_path.unlink(missing_ok=True)


def test_prettier_native_config_skips_defaults_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should skip defaults generation when native config exists.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".prettierrc").write_text("{}")
    lintro_config = LintroConfig()

    config_path = generate_defaults_config("prettier", lintro_config)

    assert_that(config_path).is_none()


def test_has_native_config_prettier_detects_prettierrc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should detect .prettierrc as native config.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".prettierrc").write_text("{}")

    assert_that(has_native_config("prettier")).is_true()


def test_get_defaults_injection_args_prettier(tmp_path: Path) -> None:
    """Should return --no-config --config args for prettier.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    config_path = tmp_path / "test.json"
    config_path.write_text("{}")
    args = get_defaults_injection_args("prettier", config_path)

    assert_that(args).is_equal_to(["--no-config", "--config", str(config_path)])
