"""Unit tests for cargo-audit plugin."""

from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.cargo_audit import (
    CARGO_AUDIT_DEFAULT_TIMEOUT,
    CargoAuditPlugin,
)


@pytest.fixture
def cargo_audit_plugin() -> CargoAuditPlugin:
    """Provide a CargoAuditPlugin instance for testing.

    Returns:
        A CargoAuditPlugin instance.
    """
    return CargoAuditPlugin()


def test_definition_name(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the tool name.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.name).is_equal_to("cargo_audit")


def test_definition_can_fix(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the tool cannot fix issues.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.can_fix).is_false()


def test_definition_tool_type(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the tool type is SECURITY.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.tool_type).is_equal_to(ToolType.SECURITY)


def test_definition_file_patterns(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the file patterns.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    patterns = cargo_audit_plugin.definition.file_patterns
    assert_that(patterns).contains("Cargo.lock")


def test_definition_priority(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the priority is 95.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.priority).is_equal_to(95)


def test_definition_timeout(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the default timeout.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.default_timeout).is_equal_to(
        CARGO_AUDIT_DEFAULT_TIMEOUT,
    )


def test_definition_native_configs(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the native config files.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    configs = cargo_audit_plugin.definition.native_configs
    assert_that(configs).contains(".cargo/audit.toml")


def test_set_options_timeout(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify timeout option can be set.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    cargo_audit_plugin.set_options(timeout=180)
    assert_that(cargo_audit_plugin.options.get("timeout")).is_equal_to(180)


def test_set_options_invalid_timeout(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify negative integer timeout raises ValueError.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    with pytest.raises(ValueError, match="non-negative"):
        cargo_audit_plugin.set_options(timeout=-1)


def test_set_options_negative_float_timeout(
    cargo_audit_plugin: CargoAuditPlugin,
) -> None:
    """Verify negative float timeout raises ValueError.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    with pytest.raises(ValueError, match="non-negative"):
        cargo_audit_plugin.set_options(timeout=-1.5)


def test_set_options_non_numeric_timeout(
    cargo_audit_plugin: CargoAuditPlugin,
) -> None:
    """Verify non-numeric timeout raises ValueError.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    with pytest.raises(ValueError, match="must be a number"):
        cargo_audit_plugin.set_options(timeout="invalid")


def test_fix_raises_not_implemented(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify fix raises NotImplementedError.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    with pytest.raises(NotImplementedError) as exc_info:
        cargo_audit_plugin.fix(["Cargo.lock"], {})
    assert_that(str(exc_info.value)).contains("cannot automatically fix")


def test_check_no_cargo_lock(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check skips gracefully when no Cargo.lock found.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    # Pass the directory itself, not a file
    # This simulates a directory without Cargo.lock

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = cargo_audit_plugin.check([str(tmp_path)], {})

    # Either no files found or no Cargo.lock found message
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_no_vulnerabilities(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no vulnerabilities found.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text('[[package]]\nname = "test"\nversion = "1.0.0"')

    mock_output = """{
        "vulnerabilities": {
            "count": 0,
            "list": []
        }
    }"""

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            cargo_audit_plugin,
            "_run_subprocess",
            return_value=(True, mock_output),
        ),
    ):
        result = cargo_audit_plugin.check([str(cargo_lock)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_vulnerabilities(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when vulnerabilities found.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text('[[package]]\nname = "test"\nversion = "1.0.0"')

    mock_output = """{
        "vulnerabilities": {
            "count": 1,
            "list": [
                {
                    "advisory": {
                        "id": "RUSTSEC-2021-0001",
                        "title": "Test vulnerability",
                        "severity": "HIGH"
                    },
                    "package": {
                        "name": "vulnerable-crate",
                        "version": "1.0.0"
                    }
                }
            ]
        }
    }"""

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            cargo_audit_plugin,
            "_run_subprocess",
            return_value=(False, mock_output),
        ),
    ):
        result = cargo_audit_plugin.check([str(cargo_lock)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_timeout(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text('[[package]]\nname = "test"\nversion = "1.0.0"')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            cargo_audit_plugin,
            "_run_subprocess",
            side_effect=TimeoutExpired(
                cmd=["cargo", "audit"],
                timeout=120,
            ),
        ),
    ):
        result = cargo_audit_plugin.check([str(cargo_lock)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
