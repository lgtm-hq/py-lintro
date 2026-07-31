"""Unit tests for BaseToolPlugin subprocess and timeout methods."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult

if TYPE_CHECKING:
    from tests.unit.plugins.conftest import FakeToolPlugin


# =============================================================================
# BaseToolPlugin._run_subprocess Tests
# =============================================================================


def test_run_subprocess_successful_command(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify successful command returns True and captured stdout.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output",
            stderr="",
        )
        success, output = fake_tool_plugin._run_subprocess(["echo", "hello"])

        assert_that(success).is_true()
        assert_that(output).is_equal_to("output")


def test_run_subprocess_failed_command(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify failed command returns False and captured stderr.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error",
        )
        success, output = fake_tool_plugin._run_subprocess(["false"])

        assert_that(success).is_false()
        assert_that(output).is_equal_to("error")


def test_run_subprocess_failed_command_without_output_includes_diagnostics(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify empty-output failures include exit code diagnostics."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=137,
            stdout="",
            stderr="",
        )
        success, output = fake_tool_plugin._run_subprocess(["mypy", "."])

        assert_that(success).is_false()
        assert_that(output).contains("exit code 137")
        assert_that(output).contains("Command: mypy .")


def test_run_subprocess_timeout_expired_raises(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify TimeoutExpired exception is raised when command times out.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["long", "cmd"],
            timeout=30,
        )

        with pytest.raises(subprocess.TimeoutExpired):
            fake_tool_plugin._run_subprocess(["long", "cmd"], timeout=30)


def test_run_subprocess_file_not_found_raises(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify FileNotFoundError is raised when command is not found.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("not found")

        with pytest.raises(FileNotFoundError, match="Command not found"):
            fake_tool_plugin._run_subprocess(["nonexistent"])


# =============================================================================
# BaseToolPlugin._get_effective_timeout Tests
# =============================================================================


@pytest.mark.parametrize(
    ("override", "options_timeout", "expected"),
    [
        pytest.param(60, None, 60.0, id="override_takes_precedence"),
        pytest.param(None, 45, 45.0, id="options_timeout_used"),
    ],
)
def test_get_effective_timeout_precedence(
    fake_tool_plugin: FakeToolPlugin,
    override: int | None,
    options_timeout: int | None,
    expected: float,
) -> None:
    """Verify timeout precedence: override > options > definition default.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
        override: The override timeout value.
        options_timeout: The options timeout value.
        expected: The expected effective timeout value.
    """
    if options_timeout is not None:
        fake_tool_plugin.options["timeout"] = options_timeout

    result = fake_tool_plugin._get_effective_timeout(override)

    assert_that(result).is_equal_to(expected)


def test_get_effective_timeout_falls_back_to_definition_default(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify timeout falls back to definition default when no override or option.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    fake_tool_plugin.options.pop("timeout", None)

    result = fake_tool_plugin._get_effective_timeout()

    assert_that(result).is_equal_to(30.0)  # FakeToolPlugin default_timeout


# =============================================================================
# BaseToolPlugin._validate_subprocess_command Tests
# =============================================================================


def test_validate_subprocess_command_valid(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify valid command list passes validation without raising.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    fake_tool_plugin._validate_subprocess_command(["ls", "-la"])
    # Should not raise


@pytest.mark.parametrize(
    ("command", "match_pattern"),
    [
        pytest.param([], "non-empty list", id="empty_list"),
        pytest.param("ls -la", "non-empty list", id="string_not_list"),
    ],
)
def test_validate_subprocess_command_invalid_structure(
    fake_tool_plugin: FakeToolPlugin,
    command: list[str] | str,
    match_pattern: str,
) -> None:
    """Verify invalid command structure raises ValueError.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        command: The command to validate.
        match_pattern: Regex pattern expected in the ValueError message.
    """
    with pytest.raises(ValueError, match=match_pattern):
        fake_tool_plugin._validate_subprocess_command(command)  # type: ignore[arg-type]


def test_validate_subprocess_command_non_string_argument(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify non-string argument in command raises ValueError.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    with pytest.raises(ValueError, match="must be strings"):
        fake_tool_plugin._validate_subprocess_command(["ls", 123])  # type: ignore[list-item]


def test_validate_subprocess_command_unsafe_characters_in_command_name(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify command with shell injection characters in name raises ValueError.

    Only the command name (first element) is validated for unsafe characters.
    Arguments can contain special characters since shell=False passes them
    literally to the subprocess.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    with pytest.raises(ValueError, match="Unsafe character"):
        fake_tool_plugin._validate_subprocess_command(["ls;rm", "-la"])


def test_validate_subprocess_command_special_chars_allowed_in_args(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify special characters in arguments are allowed with shell=False.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    # Should NOT raise - shell=False makes these safe
    fake_tool_plugin._validate_subprocess_command(["ls", "-la; rm -rf /"])
    fake_tool_plugin._validate_subprocess_command(["semgrep", "--include", "*.py"])


# =============================================================================
# BaseToolPlugin._verify_tool_version Tests
# =============================================================================


def test_verify_tool_version_passes_returns_none(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify None is returned when version check passes.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    with patch("lintro.tools.core.version_requirements.check_tool_version") as mock:
        mock.return_value = MagicMock(version_check_passed=True)

        result = fake_tool_plugin._verify_tool_version()

        assert_that(result).is_none()


def test_verify_tool_version_fails_returns_skip_result(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify skip result is returned when version check fails.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    with patch("lintro.tools.core.version_requirements.check_tool_version") as mock:
        mock.return_value = MagicMock(
            version_check_passed=False,
            current_version="0.9.0",
            error_message="Version 0.9.0 is below minimum requirement 1.0.0",
            min_version="1.0.0",
            install_hint="pip install tool",
        )

        result = fake_tool_plugin._verify_tool_version()

        assert_that(result).is_not_none()
        assert_that(result).is_instance_of(ToolResult)
        # result is verified non-None by assertpy above
        assert_that(result.output).contains("Skipping")  # type: ignore[union-attr]


def test_verify_tool_version_below_min_allowed_via_env(
    fake_tool_plugin: FakeToolPlugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below-minimum binaries proceed when listed in LINTRO_ALLOW_VERSION_LAG.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", fake_tool_plugin.definition.name)
    with patch("lintro.tools.core.version_requirements.check_tool_version") as mock:
        mock.return_value = MagicMock(
            version_check_passed=False,
            current_version="0.9.0",
            error_message="Version 0.9.0 is below minimum requirement 1.0.0",
            min_version="1.0.0",
            install_hint="pip install tool",
            below_recommended=False,
        )

        result = fake_tool_plugin._verify_tool_version()

        assert_that(result).is_none()


def test_verify_tool_version_missing_binary_not_allowed_via_env(
    fake_tool_plugin: FakeToolPlugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing binaries still skip even when the tool is version-lag allowlisted.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", fake_tool_plugin.definition.name)
    with patch("lintro.tools.core.version_requirements.check_tool_version") as mock:
        mock.return_value = MagicMock(
            version_check_passed=False,
            current_version=None,
            error_message="Tool not found",
            min_version="1.0.0",
            install_hint="pip install tool",
        )

        result = fake_tool_plugin._verify_tool_version()

        assert_that(result).is_not_none()
        assert_that(result.output).contains("Skipping")  # type: ignore[union-attr]
