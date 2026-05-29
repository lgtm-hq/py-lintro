"""Non-interactive execution tests for the astro-check plugin.

Regression coverage for issue #940: ``astro check`` hangs on the interactive
"install @astrojs/check?" prompt when no TTY is attached, timing out instead of
failing fast. The plugin now forces non-interactive execution (CI env + closed
stdin) and prefers the project-local astro binary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.astro_check import AstroCheckPlugin


@pytest.fixture
def astro_project(tmp_path: Path) -> Path:
    """Create a minimal Astro project with dependencies installed.

    A non-empty ``node_modules`` ensures the plugin does not short-circuit on
    dependency installation, so the subprocess invocation is exercised.

    Args:
        tmp_path: Temporary directory provided by pytest.

    Returns:
        Path to the project root.
    """
    (tmp_path / "astro.config.mjs").write_text("export default {};")
    (tmp_path / "package.json").write_text('{"name": "site"}')
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.astro").write_text("---\n---\n<h1>hi</h1>\n")
    node_modules = tmp_path / "node_modules" / "astro"
    node_modules.mkdir(parents=True)
    (node_modules / "package.json").write_text('{"name": "astro"}')
    return tmp_path


def test_check_runs_non_interactively(
    astro_check_plugin: AstroCheckPlugin,
    astro_project: Path,
) -> None:
    """Subprocess is invoked with CI env set and stdin closed.

    Args:
        astro_check_plugin: The plugin under test.
        astro_project: Minimal Astro project fixture.
    """
    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> tuple[bool, str]:
        captured.update(kwargs)
        return (True, "")

    with patch.object(astro_check_plugin, "_run_subprocess", side_effect=capture):
        result = astro_check_plugin.check([str(astro_project)], {})

    assert_that(result.success).is_true()
    assert_that(captured["stdin"]).is_equal_to(subprocess.DEVNULL)
    assert_that(captured["env"]["CI"]).is_equal_to("1")
    assert_that(captured["env"]["ASTRO_TELEMETRY_DISABLED"]).is_equal_to("1")


def test_check_prefers_local_astro_binary(
    astro_check_plugin: AstroCheckPlugin,
    astro_project: Path,
) -> None:
    """The project-local node_modules/.bin/astro is preferred when present.

    Args:
        astro_check_plugin: The plugin under test.
        astro_project: Minimal Astro project fixture.
    """
    bin_dir = astro_project / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    local_astro = bin_dir / "astro"
    local_astro.write_text("#!/bin/sh\n")
    local_astro.chmod(0o755)

    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> tuple[bool, str]:
        captured.update(kwargs)
        return (True, "")

    with patch.object(astro_check_plugin, "_run_subprocess", side_effect=capture):
        astro_check_plugin.check([str(astro_project)], {})

    assert_that(captured["cmd"][0]).is_equal_to(str(local_astro))
    assert_that(captured["cmd"][1]).is_equal_to("check")


def test_check_prefers_local_astro_cmd_on_windows(
    astro_check_plugin: AstroCheckPlugin,
    astro_project: Path,
) -> None:
    """On Windows, use astro.cmd instead of the non-executable shell shim.

    Args:
        astro_check_plugin: The plugin under test.
        astro_project: Minimal Astro project fixture.
    """
    bin_dir = astro_project / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "astro").write_text("#!/bin/sh\n")
    local_cmd = bin_dir / "astro.cmd"
    local_cmd.write_text("@echo off\n")

    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> tuple[bool, str]:
        captured.update(kwargs)
        return (True, "")

    with (
        patch(
            "lintro.tools.definitions.astro_check.sys.platform",
            "win32",
        ),
        patch.object(astro_check_plugin, "_run_subprocess", side_effect=capture),
    ):
        astro_check_plugin.check([str(astro_project)], {})

    assert_that(captured["cmd"][0]).is_equal_to(str(local_cmd))
    assert_that(captured["cmd"][1]).is_equal_to("check")


def test_check_windows_shell_shim_falls_back_to_global(
    astro_check_plugin: AstroCheckPlugin,
    astro_project: Path,
) -> None:
    """On Windows, a shell shim without astro.cmd falls back to global astro.

    Args:
        astro_check_plugin: The plugin under test.
        astro_project: Minimal Astro project fixture.
    """
    bin_dir = astro_project / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "astro").write_text("#!/bin/sh\n")

    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> tuple[bool, str]:
        captured.update(kwargs)
        return (True, "")

    with (
        patch(
            "lintro.tools.definitions.astro_check.sys.platform",
            "win32",
        ),
        patch(
            "lintro.tools.definitions.astro_check.shutil.which",
            side_effect=lambda name: "/usr/bin/astro" if name == "astro" else None,
        ),
        patch.object(astro_check_plugin, "_run_subprocess", side_effect=capture),
    ):
        astro_check_plugin.check([str(astro_project)], {})

    assert_that(captured["cmd"][:2]).is_equal_to(["astro", "check"])


def test_check_ignores_astro_directory_in_bin(
    astro_check_plugin: AstroCheckPlugin,
    astro_project: Path,
) -> None:
    """A directory at node_modules/.bin/astro is not treated as the binary.

    Args:
        astro_check_plugin: The plugin under test.
        astro_project: Minimal Astro project fixture.
    """
    bin_dir = astro_project / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "astro").mkdir()

    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> tuple[bool, str]:
        captured.update(kwargs)
        return (True, "")

    with (
        patch(
            "lintro.tools.definitions.astro_check.shutil.which",
            side_effect=lambda name: "/usr/bin/astro" if name == "astro" else None,
        ),
        patch.object(astro_check_plugin, "_run_subprocess", side_effect=capture),
    ):
        astro_check_plugin.check([str(astro_project)], {})

    assert_that(captured["cmd"][:2]).is_equal_to(["astro", "check"])


def test_check_falls_back_to_global_astro(
    astro_check_plugin: AstroCheckPlugin,
    astro_project: Path,
) -> None:
    """Without a local binary, a global astro executable is used.

    Args:
        astro_check_plugin: The plugin under test.
        astro_project: Minimal Astro project fixture.
    """
    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> tuple[bool, str]:
        captured.update(kwargs)
        return (True, "")

    with (
        patch(
            "lintro.tools.definitions.astro_check.shutil.which",
            side_effect=lambda name: "/usr/bin/astro" if name == "astro" else None,
        ),
        patch.object(astro_check_plugin, "_run_subprocess", side_effect=capture),
    ):
        astro_check_plugin.check([str(astro_project)], {})

    assert_that(captured["cmd"][:2]).is_equal_to(["astro", "check"])


def test_check_timeout_returns_timeout_result(
    astro_check_plugin: AstroCheckPlugin,
    astro_project: Path,
) -> None:
    """A subprocess timeout yields a timeout ToolResult rather than hanging.

    Args:
        astro_check_plugin: The plugin under test.
        astro_project: Minimal Astro project fixture.
    """

    def raise_timeout(**kwargs: Any) -> tuple[bool, str]:
        raise subprocess.TimeoutExpired(cmd=kwargs.get("cmd", []), timeout=1)

    with patch.object(
        astro_check_plugin,
        "_run_subprocess",
        side_effect=raise_timeout,
    ):
        result = astro_check_plugin.check([str(astro_project)], {"timeout": 1})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
