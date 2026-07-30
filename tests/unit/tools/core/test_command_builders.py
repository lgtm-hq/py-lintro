"""Unit tests for command_builders module."""

from __future__ import annotations

import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.enums.tool_name import ToolName
from lintro.tools.core.command_builders import (
    CargoBuilder,
    CommandBuilder,
    CommandBuilderRegistry,
    NodeJSBuilder,
    PytestBuilder,
    PythonBundledBuilder,
    StandaloneBuilder,
    find_local_node_binary,
    pinned_npm_spec,
)
from lintro.tools.core.node_fallback import (
    NODE_ENGINE_REQUIREMENTS,
    is_registry_fallback_command,
    registry_fallback_guidance,
    reset_registry_fallback_notices,
    split_npm_spec,
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
# Pinned Node.js tool resolution (issue #1727)
# =============================================================================


def _which_only(*available: str) -> Callable[..., str | None]:
    """Build a ``shutil.which`` stub that only finds the named executables.

    Args:
        *available: Executable names that should resolve.

    Returns:
        Callable usable as a ``shutil.which`` replacement.
    """

    def _which(name: str, *_args: object, **_kwargs: object) -> str | None:
        return f"/usr/local/bin/{name}" if name in available else None

    return _which


def test_html_validate_is_pinned() -> None:
    """html-validate is registered as a version-pinned Node.js tool."""
    builder = NodeJSBuilder()
    assert_that(builder.pinned_tools).contains(ToolName.HTML_VALIDATE)


def test_html_validate_prefers_local_node_modules_binary(tmp_path: Path) -> None:
    """A consumer-local install wins over any registry fetch."""
    local_bin = tmp_path / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    binary = local_bin / (
        "html-validate.cmd" if sys.platform == "win32" else "html-validate"
    )
    binary.write_text("#!/bin/sh\n")

    builder = NodeJSBuilder()
    with (
        patch("shutil.which", _which_only("bunx")),
        patch("pathlib.Path.cwd", return_value=tmp_path),
    ):
        cmd = builder.get_command("html_validate", ToolName.HTML_VALIDATE)

    assert_that(cmd).is_equal_to([binary.resolve().as_posix()])


def test_html_validate_prefers_path_binary_over_bunx() -> None:
    """A binary on PATH is used before falling back to bunx."""
    builder = NodeJSBuilder()
    with (
        patch("shutil.which", _which_only("bunx", "html-validate")),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            return_value=None,
        ),
    ):
        cmd = builder.get_command("html_validate", ToolName.HTML_VALIDATE)

    assert_that(cmd).is_equal_to(["html-validate"])


def test_html_validate_bunx_fallback_is_version_pinned() -> None:
    """The bunx fallback carries an explicit version, never ``@latest``."""
    builder = NodeJSBuilder()
    with (
        patch("shutil.which", _which_only("bunx")),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            return_value=None,
        ),
    ):
        cmd = builder.get_command("html_validate", ToolName.HTML_VALIDATE)

    expected_version = get_tool_version("html-validate")
    assert_that(expected_version).is_not_none()
    assert_that(cmd).is_equal_to(["bunx", f"html-validate@{expected_version}"])
    assert_that(cmd[1]).does_not_contain("@latest")


def test_html_validate_npx_fallback_is_version_pinned() -> None:
    """The npx fallback is pinned the same way as the bunx fallback."""
    builder = NodeJSBuilder()
    with (
        patch("shutil.which", _which_only("npx")),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            return_value=None,
        ),
    ):
        cmd = builder.get_command("html_validate", ToolName.HTML_VALIDATE)

    assert_that(cmd).is_equal_to(
        ["npx", f"html-validate@{get_tool_version('html-validate')}"],
    )


def test_html_validate_falls_back_to_bare_binary() -> None:
    """Without any package runner the bare binary name is used."""
    builder = NodeJSBuilder()
    with (
        patch("shutil.which", _which_only()),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            return_value=None,
        ),
    ):
        cmd = builder.get_command("html_validate", ToolName.HTML_VALIDATE)

    assert_that(cmd).is_equal_to(["html-validate"])


def test_pinned_npm_spec_falls_back_to_bare_name() -> None:
    """An unknown package yields a bare name rather than an ``@latest`` spec."""
    spec = pinned_npm_spec("definitely-not-a-lintro-tool")
    assert_that(spec).is_equal_to("definitely-not-a-lintro-tool")


def test_find_local_node_binary_walks_up_to_project_root(tmp_path: Path) -> None:
    """Resolution walks up so subdirectories still find the project install."""
    local_bin = tmp_path / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    binary = local_bin / (
        "html-validate.cmd" if sys.platform == "win32" else "html-validate"
    )
    binary.write_text("#!/bin/sh\n")
    nested = tmp_path / "src" / "pages"
    nested.mkdir(parents=True)

    found = find_local_node_binary("html-validate", start=nested)

    assert_that(found).is_equal_to(binary.resolve().as_posix())


def test_find_local_node_binary_returns_none_when_absent(tmp_path: Path) -> None:
    """No local install resolves to None."""
    found = find_local_node_binary("html-validate", start=tmp_path)
    assert_that(found).is_none()


def test_unpinned_node_tools_keep_bunx_behaviour() -> None:
    """Tools outside the pinned set are unaffected by the pinning branch."""
    builder = NodeJSBuilder()
    with patch("shutil.which", _which_only("bunx", "markdownlint-cli2")):
        cmd = builder.get_command("markdownlint", ToolName.MARKDOWNLINT)
    assert_that(cmd).is_equal_to(["bunx", "markdownlint-cli2"])


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


def test_html_validate_prefers_bunx_when_both_runners_are_present() -> None:
    """Bunx wins over npx when both resolve on PATH.

    The individual fallback tests each stub a single runner, so neither pins
    the precedence between them: a regression that flipped the preferred
    runner, or picked one non-deterministically, would pass both.
    """
    builder = NodeJSBuilder()
    with (
        # Both runners present, but the tool itself is NOT on PATH — a PATH
        # hit would correctly win before either runner and mask the ordering.
        patch(
            "shutil.which",
            lambda name: f"/usr/bin/{name}" if name in {"bunx", "npx"} else None,
        ),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            return_value=None,
        ),
    ):
        cmd = builder.get_command("html_validate", ToolName.HTML_VALIDATE)

    assert_that(cmd[0]).is_equal_to("bunx")


def test_registry_resolves_node_tools_from_the_given_cwd() -> None:
    """A supplied cwd scopes node_modules/.bin resolution to that directory.

    Without it the search starts at lintro's own working directory, which can
    select an unrelated install ahead of PATH (#1727).
    """
    seen: list[Path | None] = []

    def _record(binary_name: str, *, start: Path | None = None) -> None:
        seen.append(start)
        return None

    target = Path("/tmp/some-target-project")
    with (
        patch("shutil.which", _which_only("bunx")),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            side_effect=_record,
        ),
    ):
        CommandBuilderRegistry.get_command(
            "html_validate",
            ToolName.HTML_VALIDATE,
            target,
        )

    # Exactly one lookup, scoped to the target — no second, process-cwd search.
    assert_that(seen).is_equal_to([target])


def test_registry_without_cwd_preserves_process_relative_resolution() -> None:
    """Omitting cwd keeps the previous behaviour for every builder."""
    seen: list[Path | None] = []

    def _record(binary_name: str, *, start: Path | None = None) -> None:
        seen.append(start)
        return None

    with (
        patch("shutil.which", _which_only("bunx")),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            side_effect=_record,
        ),
    ):
        CommandBuilderRegistry.get_command("html_validate", ToolName.HTML_VALIDATE)

    assert_that(seen).is_equal_to([None])


# =============================================================================
# Execution-directory (cwd) resolution contract, per builder (#1758)
# =============================================================================


def test_node_get_command_matches_get_command_in_without_cwd() -> None:
    """``get_command`` is ``get_command_in`` with no execution directory.

    The pinned path used to reach ``_get_pinned_command`` twice, once with a
    ``start`` and once without, so a direct caller could get a different search
    origin than the registry. Both now share one resolver (#1758).
    """
    builder = NodeJSBuilder()
    seen: list[Path | None] = []

    def _record(binary_name: str, *, start: Path | None = None) -> str | None:
        seen.append(start)
        return None

    with (
        patch("shutil.which", _which_only("bunx")),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            side_effect=_record,
        ),
    ):
        direct = builder.get_command("html_validate", ToolName.HTML_VALIDATE)
        threaded = builder.get_command_in(
            "html_validate",
            ToolName.HTML_VALIDATE,
            None,
        )

    assert_that(direct).is_equal_to(threaded)
    assert_that(seen).is_equal_to([None, None])


def test_python_bundled_builder_ignores_the_execution_directory(
    tmp_path: Path,
) -> None:
    """Bundled Python tools resolve from Lintro's own environment.

    They are Lintro's declared dependencies, gated on the manifest minimum and
    parsed by version-specific parsers, so a checked project's virtualenv must
    not win. Locks the documented decision for #1758.
    """
    builder = PythonBundledBuilder()
    with patch("shutil.which", _which_only("ruff")):
        external = builder.get_command_in("ruff", ToolName.RUFF, tmp_path)
        process_relative = builder.get_command("ruff", ToolName.RUFF)

    assert_that(external).is_equal_to(process_relative)


def test_pytest_builder_ignores_the_execution_directory(tmp_path: Path) -> None:
    """Pytest resolves from the running interpreter, not the target tree.

    Walking up from the execution directory for a ``.venv`` would select an
    interpreter whose plugins and dependencies Lintro knows nothing about.
    Locks the documented decision for #1758.
    """
    builder = PytestBuilder()
    with patch("shutil.which", _which_only("pytest")):
        external = builder.get_command_in("pytest", ToolName.PYTEST, tmp_path)
        process_relative = builder.get_command("pytest", ToolName.PYTEST)

    assert_that(external).is_equal_to(process_relative)


def test_cargo_builder_ignores_the_execution_directory(tmp_path: Path) -> None:
    """Cargo resolves from PATH; cargo itself does the project-relative work.

    Workspace root, ``target/`` and ``rust-toolchain.toml`` are found by cargo
    and rustup from the cwd the executor sets, so the command never varies.
    Locks the documented decision for #1758.
    """
    builder = CargoBuilder()
    external = builder.get_command_in("clippy", ToolName.CLIPPY, tmp_path)

    assert_that(external).is_equal_to(["cargo", "clippy"])
    assert_that(external).is_equal_to(builder.get_command("clippy", ToolName.CLIPPY))


def test_standalone_builder_ignores_the_execution_directory(tmp_path: Path) -> None:
    """Standalone binaries resolve against PATH only.

    None of these ecosystems define a project-local install directory, so there
    is no per-directory candidate to prefer. Locks the decision for #1758.
    """
    builder = StandaloneBuilder()
    external = builder.get_command_in("hadolint", ToolName.HADOLINT, tmp_path)

    assert_that(external).is_equal_to(["hadolint"])
    assert_that(external).is_equal_to(
        builder.get_command("hadolint", ToolName.HADOLINT),
    )


# =============================================================================
# Registry fallback guidance (#1767)
# =============================================================================


@pytest.fixture
def clean_fallback_notices() -> Generator[None, None, None]:
    """Reset the one-time fallback notice cache around a test.

    Yields:
        None: With the notice cache cleared before and after the test.
    """
    reset_registry_fallback_notices()
    yield
    reset_registry_fallback_notices()


def test_is_registry_fallback_command_detects_package_runners() -> None:
    """Only ``bunx``/``npx`` invocations count as the registry fallback."""
    assert_that(is_registry_fallback_command(["bunx", "html-validate@1.0.0"])).is_true()
    assert_that(is_registry_fallback_command(["npx", "html-validate@1.0.0"])).is_true()
    assert_that(is_registry_fallback_command(["/local/html-validate"])).is_false()
    assert_that(is_registry_fallback_command(["html-validate"])).is_false()
    assert_that(is_registry_fallback_command(["bunx"])).is_false()


def test_split_npm_spec_handles_scoped_packages() -> None:
    """The version separator is the last ``@``, not the scope marker."""
    assert_that(split_npm_spec("html-validate@11.5.6")).is_equal_to(
        ("html-validate", "11.5.6"),
    )
    assert_that(split_npm_spec("@scope/pkg@1.2.3")).is_equal_to(
        ("@scope/pkg", "1.2.3"),
    )
    assert_that(split_npm_spec("html-validate")).is_equal_to(("html-validate", None))


def test_registry_fallback_guidance_names_local_install_and_node_floor() -> None:
    """A failed fallback is explained with the pinned local install commands."""
    spec = pinned_npm_spec("html-validate")
    guidance = registry_fallback_guidance(["bunx", spec])

    assert_that(guidance).contains(f"could not be run via `bunx {spec}`")
    assert_that(guidance).contains(f"bun add -D {spec}")
    assert_that(guidance).contains(f"npm install -D {spec}")
    assert_that(guidance).contains(
        f"requires Node {NODE_ENGINE_REQUIREMENTS['html-validate']}",
    )
    # The pinned version is derived, never hardcoded in the message builder.
    assert_that(spec).contains(str(get_tool_version("html-validate")))


def test_registry_fallback_guidance_omits_unknown_node_floor() -> None:
    """Packages with no recorded ``engines`` floor get no Node note."""
    guidance = registry_fallback_guidance(["npx", "some-linter@1.0.0"])

    assert_that(guidance).contains("npm install -D some-linter@1.0.0")
    assert_that(guidance).does_not_contain("requires Node")


def test_html_validate_bunx_fallback_warns_once(
    clean_fallback_notices: None,
) -> None:
    """Selecting the bunx fallback warns, and only once per process.

    Args:
        clean_fallback_notices: Fixture clearing the one-time notice cache.
    """
    builder = NodeJSBuilder()
    with (
        patch("shutil.which", _which_only("bunx")),
        patch(
            "lintro.tools.core.command_builders.find_local_node_binary",
            return_value=None,
        ),
        patch("lintro.tools.core.node_fallback.logger") as mock_logger,
    ):
        first = builder.get_command("html_validate", ToolName.HTML_VALIDATE)
        second = builder.get_command("html_validate", ToolName.HTML_VALIDATE)

    assert_that(first).is_equal_to(second)
    assert_that(mock_logger.warning.call_count).is_equal_to(1)
    message = cast(str, mock_logger.warning.call_args.args[0])
    assert_that(message).contains("No project-local or PATH install of html-validate")
    assert_that(message).contains(f"bun add -D {pinned_npm_spec('html-validate')}")
    assert_that(message).contains(
        NODE_ENGINE_REQUIREMENTS["html-validate"],
    )


def test_local_install_emits_no_fallback_notice(
    clean_fallback_notices: None,
    tmp_path: Path,
) -> None:
    """A project-local install is the good path and stays silent.

    Args:
        clean_fallback_notices: Fixture clearing the one-time notice cache.
        tmp_path: Temporary project root holding the local install.
    """
    local_bin = tmp_path / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    binary = local_bin / (
        "html-validate.cmd" if sys.platform == "win32" else "html-validate"
    )
    binary.write_text("#!/bin/sh\n")

    builder = NodeJSBuilder()
    with (
        patch("shutil.which", _which_only("bunx")),
        patch("lintro.tools.core.node_fallback.logger") as mock_logger,
    ):
        cmd = builder.get_command_in(
            "html_validate",
            ToolName.HTML_VALIDATE,
            tmp_path,
        )

    assert_that(cmd).is_equal_to([binary.resolve().as_posix()])
    assert_that(mock_logger.warning.call_count).is_equal_to(0)
