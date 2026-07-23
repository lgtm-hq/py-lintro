"""Unit tests for command_builders module."""

from __future__ import annotations

import sys
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.tools.core.command_builders import (
    CargoBuilder,
    CommandBuilder,
    CommandBuilderRegistry,
    NodeJSBuilder,
    PytestBuilder,
    PythonBundledBuilder,
    StandaloneBuilder,
)


def _mock_which_for_venv(
    *,
    in_venv: bool,
    in_path: str | None = None,
    expected_names: str | set[str],
) -> MagicMock:
    """Create a shutil.which mock that controls venv vs PATH discovery.

    When in_venv is True, shutil.which(tool, path=scripts_dir) returns
    a path (simulating the tool being in the venv). When False, it returns
    None for the venv lookup but returns in_path for the PATH lookup.
    The mock validates that the requested name matches expected_names and
    that the scripts directory path looks correct before returning results.

    Args:
        in_venv: Whether the tool should be found in the venv scripts dir.
        in_path: Path to return for PATH-based discovery (None = not found).
        expected_names: Executable name(s) this mock should respond to.

    Returns:
        Mock to use with patch("shutil.which", ...).
    """
    names = {expected_names} if isinstance(expected_names, str) else expected_names

    def which_side_effect(
        name: str,
        path: str | None = None,
    ) -> str | None:
        if path is not None:
            # Venv scripts lookup: validate name and path
            if name not in names:
                return None
            if not path.endswith(("/bin", "\\Scripts")):
                return None
            return f"/fake/venv/bin/{name}" if in_venv else None
        # PATH lookup: validate name
        if name not in names:
            return None
        return in_path

    return MagicMock(side_effect=which_side_effect)


@pytest.fixture(autouse=True)
def reset_registry() -> Generator[None, None, None]:
    """Reset the command builder registry before and after each test.

    Yields:
        None: After clearing the registry and before restoring.
    """
    original_builders = CommandBuilderRegistry._builders.copy()
    yield
    CommandBuilderRegistry._builders = original_builders


# =============================================================================
# PythonBundledBuilder tests
# =============================================================================


def test_python_bundled_builder_handles_ruff() -> None:
    """PythonBundledBuilder can handle ruff."""
    builder = PythonBundledBuilder()
    assert_that(builder.can_handle(ToolName.RUFF)).is_true()


def test_python_bundled_builder_handles_black() -> None:
    """PythonBundledBuilder can handle black."""
    builder = PythonBundledBuilder()
    assert_that(builder.can_handle(ToolName.BLACK)).is_true()


def test_python_bundled_builder_handles_mypy() -> None:
    """PythonBundledBuilder can handle mypy."""
    builder = PythonBundledBuilder()
    assert_that(builder.can_handle(ToolName.MYPY)).is_true()


def test_python_bundled_builder_does_not_handle_markdownlint() -> None:
    """PythonBundledBuilder does not handle Node.js tools."""
    builder = PythonBundledBuilder()
    assert_that(builder.can_handle(ToolName.MARKDOWNLINT)).is_false()


def test_python_bundled_builder_prefers_path_binary_outside_venv() -> None:
    """PythonBundledBuilder prefers PATH binary when outside venv."""
    builder = PythonBundledBuilder()
    # Simulate running outside a venv (prefix == base_prefix)
    with (
        patch("shutil.which", return_value="/usr/local/bin/ruff"),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
    ):
        cmd = builder.get_command("ruff", ToolName.RUFF)
        assert_that(cmd).is_equal_to(["/usr/local/bin/ruff"])


def test_python_bundled_builder_prefers_python_module_in_venv() -> None:
    """PythonBundledBuilder prefers python -m when tool is in venv scripts."""
    builder = PythonBundledBuilder()
    # Simulate running inside a venv with the tool present in venv scripts
    with (
        patch(
            "shutil.which",
            _mock_which_for_venv(in_venv=True, expected_names="ruff"),
        ),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/app/.venv",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
        patch(
            "lintro.tools.core.command_builders.sysconfig.get_path",
            return_value="/app/.venv/bin",
        ),
    ):
        cmd = builder.get_command("ruff", ToolName.RUFF)
        # Should return [python_exe, "-m", "ruff"] when tool is in venv
        assert_that(cmd).is_length(3)
        assert_that(cmd[0]).is_equal_to(sys.executable)
        assert_that(cmd[1]).is_equal_to("-m")
        assert_that(cmd[2]).is_equal_to("ruff")


def test_python_bundled_builder_prefers_path_when_tool_not_in_venv() -> None:
    """PythonBundledBuilder uses PATH when tool is not in venv (Homebrew)."""
    builder = PythonBundledBuilder()
    # Simulate Homebrew: in a venv, but tool is a separate Homebrew formula
    with (
        patch(
            "shutil.which",
            _mock_which_for_venv(
                in_venv=False,
                in_path="/opt/homebrew/bin/ruff",
                expected_names="ruff",
            ),
        ),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/opt/homebrew/Cellar/lintro/0.57.7/libexec",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/opt/homebrew/Cellar/python@3.13/3.13.0/Frameworks",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
        patch(
            "lintro.tools.core.command_builders.sysconfig.get_path",
            return_value="/opt/homebrew/Cellar/lintro/0.57.7/libexec/bin",
        ),
    ):
        cmd = builder.get_command("ruff", ToolName.RUFF)
        assert_that(cmd).is_equal_to(["/opt/homebrew/bin/ruff"])


def test_python_bundled_builder_last_resort_python_m_in_venv() -> None:
    """PythonBundledBuilder falls back to python -m when tool nowhere."""
    builder = PythonBundledBuilder()
    # In a venv, tool NOT in venv scripts, NOT in PATH
    with (
        patch(
            "shutil.which",
            _mock_which_for_venv(in_venv=False, in_path=None, expected_names="ruff"),
        ),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/opt/homebrew/Cellar/lintro/0.57.7/libexec",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/opt/homebrew/Cellar/python@3.13/3.13.0/Frameworks",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
        patch(
            "lintro.tools.core.command_builders.sysconfig.get_path",
            return_value="/opt/homebrew/Cellar/lintro/0.57.7/libexec/bin",
        ),
    ):
        cmd = builder.get_command("ruff", ToolName.RUFF)
        # Last resort: python -m
        assert_that(cmd).is_length(3)
        assert_that(cmd[0]).is_equal_to(sys.executable)
        assert_that(cmd[1]).is_equal_to("-m")
        assert_that(cmd[2]).is_equal_to("ruff")


def test_python_bundled_builder_falls_back_to_python_module() -> None:
    """PythonBundledBuilder falls back to python -m when tool not in PATH."""
    builder = PythonBundledBuilder()
    with (
        patch("shutil.which", return_value=None),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
    ):
        cmd = builder.get_command("ruff", ToolName.RUFF)
        # Should return [python_exe, "-m", "ruff"]
        assert_that(cmd).is_length(3)
        assert_that(cmd[0]).is_equal_to(sys.executable)
        assert_that(cmd[1]).is_equal_to("-m")
        assert_that(cmd[2]).is_equal_to("ruff")


def test_python_bundled_builder_skips_python_module_when_compiled() -> None:
    """PythonBundledBuilder skips python -m fallback when compiled."""
    builder = PythonBundledBuilder()
    with (
        patch("shutil.which", return_value=None),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=True,
        ),
    ):
        cmd = builder.get_command("ruff", ToolName.RUFF)
        # Should return just [tool_name] when compiled
        assert_that(cmd).is_equal_to(["ruff"])


# =============================================================================
# PytestBuilder tests
# =============================================================================


def test_pytest_builder_handles_pytest() -> None:
    """PytestBuilder can handle pytest."""
    builder = PytestBuilder()
    assert_that(builder.can_handle(ToolName.PYTEST)).is_true()


def test_pytest_builder_does_not_handle_ruff() -> None:
    """PytestBuilder does not handle ruff."""
    builder = PytestBuilder()
    assert_that(builder.can_handle(ToolName.RUFF)).is_false()


def test_pytest_builder_prefers_path_binary_outside_venv() -> None:
    """PytestBuilder prefers PATH binary when outside venv."""
    builder = PytestBuilder()
    # Simulate running outside a venv (prefix == base_prefix)
    with (
        patch("shutil.which", return_value="/usr/local/bin/pytest"),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
    ):
        cmd = builder.get_command("pytest", ToolName.PYTEST)
        assert_that(cmd).is_equal_to(["/usr/local/bin/pytest"])


def test_pytest_builder_prefers_python_module_in_venv() -> None:
    """PytestBuilder prefers python -m pytest when tool is in venv scripts."""
    builder = PytestBuilder()
    # Simulate running inside a venv with pytest present in venv scripts
    with (
        patch(
            "shutil.which",
            _mock_which_for_venv(in_venv=True, expected_names="pytest"),
        ),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/app/.venv",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
        patch(
            "lintro.tools.core.command_builders.sysconfig.get_path",
            return_value="/app/.venv/bin",
        ),
    ):
        cmd = builder.get_command("pytest", ToolName.PYTEST)
        # Should return [python_exe, "-m", "pytest"] when tool is in venv
        assert_that(cmd).is_length(3)
        assert_that(cmd[0]).is_equal_to(sys.executable)
        assert_that(cmd[1]).is_equal_to("-m")
        assert_that(cmd[2]).is_equal_to("pytest")


def test_pytest_builder_prefers_path_when_tool_not_in_venv() -> None:
    """PytestBuilder uses PATH when pytest is not in venv (Homebrew)."""
    builder = PytestBuilder()
    with (
        patch(
            "shutil.which",
            _mock_which_for_venv(
                in_venv=False,
                in_path="/opt/homebrew/bin/pytest",
                expected_names="pytest",
            ),
        ),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/opt/homebrew/Cellar/lintro/0.57.7/libexec",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/opt/homebrew/Cellar/python@3.13/3.13.0/Frameworks",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
        patch(
            "lintro.tools.core.command_builders.sysconfig.get_path",
            return_value="/opt/homebrew/Cellar/lintro/0.57.7/libexec/bin",
        ),
    ):
        cmd = builder.get_command("pytest", ToolName.PYTEST)
        assert_that(cmd).is_equal_to(["/opt/homebrew/bin/pytest"])


def test_pytest_builder_last_resort_python_m_in_venv() -> None:
    """PytestBuilder falls back to python -m when pytest nowhere."""
    builder = PytestBuilder()
    with (
        patch(
            "shutil.which",
            _mock_which_for_venv(in_venv=False, in_path=None, expected_names="pytest"),
        ),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/opt/homebrew/Cellar/lintro/0.57.7/libexec",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/opt/homebrew/Cellar/python@3.13/3.13.0/Frameworks",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
        patch(
            "lintro.tools.core.command_builders.sysconfig.get_path",
            return_value="/opt/homebrew/Cellar/lintro/0.57.7/libexec/bin",
        ),
    ):
        cmd = builder.get_command("pytest", ToolName.PYTEST)
        assert_that(cmd).is_length(3)
        assert_that(cmd[0]).is_equal_to(sys.executable)
        assert_that(cmd[1]).is_equal_to("-m")
        assert_that(cmd[2]).is_equal_to("pytest")


def test_pytest_builder_falls_back_to_python_module() -> None:
    """PytestBuilder falls back to python -m pytest when not in PATH."""
    builder = PytestBuilder()
    with (
        patch("shutil.which", return_value=None),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
    ):
        cmd = builder.get_command("pytest", ToolName.PYTEST)
        # Should return [python_exe, "-m", "pytest"]
        assert_that(cmd).is_length(3)
        assert_that(cmd[0]).is_equal_to(sys.executable)
        assert_that(cmd[1]).is_equal_to("-m")
        assert_that(cmd[2]).is_equal_to("pytest")


def test_pytest_builder_skips_python_module_when_compiled() -> None:
    """PytestBuilder skips python -m fallback when compiled."""
    builder = PytestBuilder()
    with (
        patch("shutil.which", return_value=None),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=True,
        ),
    ):
        cmd = builder.get_command("pytest", ToolName.PYTEST)
        # Should return just ["pytest"] when compiled
        assert_that(cmd).is_equal_to(["pytest"])


# =============================================================================
# NodeJSBuilder tests
# =============================================================================


def test_nodejs_builder_handles_markdownlint() -> None:
    """NodeJSBuilder can handle markdownlint."""
    builder = NodeJSBuilder()
    assert_that(builder.can_handle(ToolName.MARKDOWNLINT)).is_true()


def test_nodejs_builder_handles_astro_check() -> None:
    """NodeJSBuilder can handle astro-check."""
    builder = NodeJSBuilder()
    assert_that(builder.can_handle(ToolName.ASTRO_CHECK)).is_true()


def test_nodejs_builder_does_not_handle_ruff() -> None:
    """NodeJSBuilder does not handle Python tools."""
    builder = NodeJSBuilder()
    assert_that(builder.can_handle(ToolName.RUFF)).is_false()


def test_nodejs_builder_uses_bunx_when_available() -> None:
    """NodeJSBuilder uses bunx when available."""
    builder = NodeJSBuilder()
    with patch("shutil.which", return_value="/usr/local/bin/bunx"):
        cmd = builder.get_command("markdownlint", ToolName.MARKDOWNLINT)
        assert_that(cmd).is_equal_to(["bunx", "markdownlint-cli2"])


def test_nodejs_builder_falls_back_to_package_name() -> None:
    """NodeJSBuilder falls back to package name when bunx not available."""
    builder = NodeJSBuilder()
    with patch("shutil.which", return_value=None):
        cmd = builder.get_command("markdownlint", ToolName.MARKDOWNLINT)
        assert_that(cmd).is_equal_to(["markdownlint-cli2"])


def test_nodejs_builder_astro_check_uses_astro_binary() -> None:
    """NodeJSBuilder resolves astro-check to astro binary."""
    builder = NodeJSBuilder()
    with patch("shutil.which", return_value="/usr/local/bin/bunx"):
        cmd = builder.get_command("astro-check", ToolName.ASTRO_CHECK)
        assert_that(cmd).is_equal_to(["bunx", "astro"])


def test_nodejs_builder_handles_vue_tsc() -> None:
    """NodeJSBuilder can handle vue-tsc."""
    builder = NodeJSBuilder()
    assert_that(builder.can_handle(ToolName.VUE_TSC)).is_true()


def test_nodejs_builder_vue_tsc_uses_vue_tsc_binary() -> None:
    """NodeJSBuilder resolves vue-tsc to vue-tsc binary."""
    builder = NodeJSBuilder()
    with patch("shutil.which", return_value="/usr/local/bin/bunx"):
        cmd = builder.get_command("vue-tsc", ToolName.VUE_TSC)
        assert_that(cmd).is_equal_to(["bunx", "vue-tsc"])


# =============================================================================
# CargoBuilder tests
# =============================================================================


def test_cargo_builder_handles_clippy() -> None:
    """CargoBuilder can handle clippy."""
    builder = CargoBuilder()
    assert_that(builder.can_handle(ToolName.CLIPPY)).is_true()


def test_cargo_builder_does_not_handle_ruff() -> None:
    """CargoBuilder does not handle Python tools."""
    builder = CargoBuilder()
    assert_that(builder.can_handle(ToolName.RUFF)).is_false()


def test_cargo_builder_returns_cargo_clippy() -> None:
    """CargoBuilder returns ['cargo', 'clippy'] command."""
    builder = CargoBuilder()
    cmd = builder.get_command("clippy", ToolName.CLIPPY)
    assert_that(cmd).is_equal_to(["cargo", "clippy"])


def test_cargo_builder_handles_cargo_audit() -> None:
    """CargoBuilder can handle cargo_audit."""
    builder = CargoBuilder()
    assert_that(builder.can_handle(ToolName.CARGO_AUDIT)).is_true()


def test_cargo_builder_returns_cargo_audit() -> None:
    """CargoBuilder returns ['cargo', 'audit'] command for cargo_audit."""
    builder = CargoBuilder()
    cmd = builder.get_command("cargo_audit", ToolName.CARGO_AUDIT)
    assert_that(cmd).is_equal_to(["cargo", "audit"])


# =============================================================================
# StandaloneBuilder tests
# =============================================================================


def test_standalone_builder_handles_hadolint() -> None:
    """StandaloneBuilder can handle hadolint."""
    builder = StandaloneBuilder()
    assert_that(builder.can_handle(ToolName.HADOLINT)).is_true()


def test_standalone_builder_handles_actionlint() -> None:
    """StandaloneBuilder can handle actionlint."""
    builder = StandaloneBuilder()
    assert_that(builder.can_handle(ToolName.ACTIONLINT)).is_true()


def test_standalone_builder_does_not_handle_ruff() -> None:
    """StandaloneBuilder does not handle Python bundled tools."""
    builder = StandaloneBuilder()
    assert_that(builder.can_handle(ToolName.RUFF)).is_false()


def test_standalone_builder_returns_tool_name() -> None:
    """StandaloneBuilder returns just the tool name."""
    builder = StandaloneBuilder()
    cmd = builder.get_command("hadolint", ToolName.HADOLINT)
    assert_that(cmd).is_equal_to(["hadolint"])


def test_standalone_builder_handles_pip_audit() -> None:
    """StandaloneBuilder can handle pip_audit."""
    builder = StandaloneBuilder()
    assert_that(builder.can_handle(ToolName.PIP_AUDIT)).is_true()


def test_standalone_builder_maps_pip_audit_to_hyphenated_binary() -> None:
    """pip_audit resolves to the ``pip-audit`` binary, not ``pip_audit``.

    The internal tool name uses an underscore, but the installed executable
    is ``pip-audit``; without the binary mapping the version check would exec
    a nonexistent ``pip_audit`` and the tool would always skip.
    """
    builder = StandaloneBuilder()
    cmd = builder.get_command("pip_audit", ToolName.PIP_AUDIT)
    assert_that(cmd).is_equal_to(["pip-audit"])


# =============================================================================
# CommandBuilderRegistry tests
# =============================================================================


def test_registry_uses_first_matching_builder() -> None:
    """Registry returns command from first builder that can_handle()."""
    CommandBuilderRegistry.clear()

    # Register a custom builder that handles ruff
    class CustomRuffBuilder(CommandBuilder):
        def can_handle(self, tool_name_enum: ToolName | None) -> bool:
            return tool_name_enum == ToolName.RUFF

        def get_command(
            self,
            tool_name: str,
            tool_name_enum: ToolName | None,
        ) -> list[str]:
            return ["custom-ruff"]

    CommandBuilderRegistry.register(CustomRuffBuilder())
    CommandBuilderRegistry.register(PythonBundledBuilder())

    cmd = CommandBuilderRegistry.get_command("ruff", ToolName.RUFF)
    assert_that(cmd).is_equal_to(["custom-ruff"])


def test_registry_fallback_to_tool_name() -> None:
    """Registry falls back to [tool_name] if no builder matches."""
    CommandBuilderRegistry.clear()

    cmd = CommandBuilderRegistry.get_command("unknown_tool", None)
    assert_that(cmd).is_equal_to(["unknown_tool"])


def test_registry_is_registered() -> None:
    """Registry can check if a builder exists for a tool."""
    CommandBuilderRegistry.clear()
    CommandBuilderRegistry.register(PythonBundledBuilder())

    assert_that(CommandBuilderRegistry.is_registered(ToolName.RUFF)).is_true()
    assert_that(CommandBuilderRegistry.is_registered(ToolName.MARKDOWNLINT)).is_false()


def test_registry_clear() -> None:
    """Registry clear removes all builders."""
    CommandBuilderRegistry.clear()
    CommandBuilderRegistry.register(PythonBundledBuilder())

    assert_that(CommandBuilderRegistry._builders).is_length(1)

    CommandBuilderRegistry.clear()
    assert_that(CommandBuilderRegistry._builders).is_empty()
