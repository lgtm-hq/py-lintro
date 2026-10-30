"""Unit tests for cargo-deny plugin."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.tools.cargo_deny.definition import CargoDenyPlugin

_ERROR_DIAGNOSTIC = (
    '{"type":"diagnostic","fields":{"severity":"error","code":"L001",'
    '"message":"license is not allowed"}}'
)
_WARNING_DIAGNOSTIC = (
    '{"type":"diagnostic","fields":{"severity":"warning","code":"B001",'
    '"message":"duplicate dependency"}}'
)
_INFORMATIONAL_OUTPUT = (
    '{"type":"log","fields":{"level":"INFO","message":"checking advisories"}}\n'
    '{"type":"summary","fields":{"advisories":'
    '{"errors":0,"warnings":0,"notes":0,"helps":0}}}'
)


@pytest.fixture
def cargo_deny_plugin() -> CargoDenyPlugin:
    """Provide a CargoDenyPlugin instance for testing.

    Returns:
        A CargoDenyPlugin instance.
    """
    return CargoDenyPlugin()


@pytest.fixture
def cargo_deny_check(
    cargo_deny_plugin: CargoDenyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Callable[[bool, str], ToolResult]:
    """Run the plugin on a real manifest with a controlled process result.

    Args:
        cargo_deny_plugin: The plugin instance.
        monkeypatch: Fixture restoring process and version-check patches.
        tmp_path: Temporary Cargo project directory.

    Returns:
        A callable accepting the process success flag and captured output.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[package]\nname = "example"\nversion = "0.1.0"\n')
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        MagicMock(return_value=None),
    )

    def check(process_success: bool, output: str) -> ToolResult:
        """Check the manifest using the supplied subprocess result.

        Args:
            process_success: Whether the process exited zero.
            output: Combined tool output.

        Returns:
            The plugin's observable result.
        """
        monkeypatch.setattr(
            cargo_deny_plugin,
            "_run_subprocess",
            MagicMock(return_value=(process_success, output)),
        )
        return cargo_deny_plugin.check([str(manifest)], {})

    return check


@pytest.mark.parametrize(
    ("process_success", "output", "expected_success", "expected_messages"),
    [
        pytest.param(False, "error: invalid configuration\n", False, [], id="garbage"),
        pytest.param(False, "", False, [], id="failed-empty"),
        pytest.param(
            False,
            _ERROR_DIAGNOSTIC,
            False,
            ["license is not allowed"],
            id="error",
        ),
        pytest.param(
            False,
            _WARNING_DIAGNOSTIC,
            False,
            ["duplicate dependency"],
            id="failed-warning-only",
        ),
        pytest.param(False, _INFORMATIONAL_OUTPUT, False, [], id="failed-summary"),
        pytest.param(True, "", True, [], id="clean-empty"),
        pytest.param(True, _INFORMATIONAL_OUTPUT, True, [], id="clean-summary"),
        pytest.param(
            True,
            _ERROR_DIAGNOSTIC,
            False,
            ["license is not allowed"],
            id="error-despite-zero-exit",
        ),
    ],
)
def test_check_exit_status(
    cargo_deny_check: Callable[[bool, str], ToolResult],
    process_success: bool,
    output: str,
    expected_success: bool,
    expected_messages: list[str],
) -> None:
    """Preserve failed invocations and report parsed findings.

    Args:
        cargo_deny_check: Callable running the plugin with controlled output.
        process_success: Whether the tool exited zero.
        output: Captured tool output.
        expected_success: Expected overall check verdict.
        expected_messages: Expected parsed finding messages.
    """
    result = cargo_deny_check(process_success, output)

    assert_that(result.no_files).is_false()
    assert_that(result.skipped).is_false()
    assert_that(result.success).is_equal_to(expected_success)
    assert_that(result.issues_count).is_equal_to(len(expected_messages))
    assert_that([issue.message for issue in result.issues or []]).is_equal_to(
        expected_messages,
    )
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.output).is_equal_to(None if process_success else output)


@pytest.mark.parametrize("process_success", [True, False], ids=["exit-0", "exit-1"])
@pytest.mark.parametrize(
    "output",
    [
        pytest.param('{"type":"diagnostic","fields":null}', id="null-fields"),
        pytest.param(
            '{"type":"diagnostic","fields":{"severity":null}}',
            id="null-severity",
        ),
        pytest.param('{"type":"advisory","fields":null}', id="null-advisory-fields"),
        pytest.param(
            '{"type":"advisory","fields":{"advisory":null}}',
            id="null-advisory",
        ),
        pytest.param(
            '{"type":null,"fields":{"severity":"error","message":"lost"}}',
            id="null-type",
        ),
        pytest.param('{"type":null,"fields":null}', id="null-type-and-fields"),
        pytest.param('{"fields":{"severity":"error"}}', id="missing-type"),
        pytest.param(
            '{"type":"unexpected","fields":{"severity":"error"}}',
            id="unknown-error-type",
        ),
        pytest.param('{"type":"diagnostic","fields":', id="truncated-json"),
    ],
)
def test_check_malformed_records_fail_closed(
    cargo_deny_check: Callable[[bool, str], ToolResult],
    process_success: bool,
    output: str,
) -> None:
    """Expose a malformed diagnostic alongside legitimate information records.

    Args:
        cargo_deny_check: Callable running the plugin with controlled output.
        process_success: Whether the tool exited zero.
        output: A malformed error-bearing JSON record.
    """
    combined = f"{_INFORMATIONAL_OUTPUT}\n{output}"
    result = cargo_deny_check(process_success, combined)

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.issues).is_empty()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).is_equal_to(combined)


def test_definition_name(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the tool name.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.name).is_equal_to("cargo_deny")


def test_definition_can_fix(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the tool cannot fix issues.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.can_fix).is_false()


def test_definition_tool_type(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the tool type is SECURITY | INFRASTRUCTURE.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    expected_type = ToolType.SECURITY | ToolType.INFRASTRUCTURE
    assert_that(cargo_deny_plugin.definition.tool_type).is_equal_to(expected_type)


def test_definition_file_patterns(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the file patterns.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    patterns = cargo_deny_plugin.definition.file_patterns
    assert_that(patterns).contains("Cargo.toml")
    assert_that(patterns).contains("deny.toml")


def test_definition_timeout(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the default timeout is 60.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.default_timeout).is_equal_to(60)


def test_definition_native_configs(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify the native config files.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    assert_that(cargo_deny_plugin.definition.native_configs).contains("deny.toml")


def test_fix_raises_not_implemented(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify fix raises NotImplementedError.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    with pytest.raises(NotImplementedError) as exc_info:
        cargo_deny_plugin.fix(["."], {})
    assert_that(str(exc_info.value)).contains("cannot automatically fix")


def test_set_options_timeout(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify timeout option can be set.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    cargo_deny_plugin.set_options(timeout=120)
    assert_that(cargo_deny_plugin.options.get("timeout")).is_equal_to(120)


def test_set_options_invalid_timeout(cargo_deny_plugin: CargoDenyPlugin) -> None:
    """Verify invalid timeout raises ValueError.

    Args:
        cargo_deny_plugin: The plugin instance.
    """
    with pytest.raises(ValueError):
        cargo_deny_plugin.set_options(timeout=-1)
