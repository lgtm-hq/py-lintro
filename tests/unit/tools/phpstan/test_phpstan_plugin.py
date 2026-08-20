"""Unit tests for the PHPStan tool plugin."""

from __future__ import annotations

import json
import os
import stat
import subprocess  # nosec B404 - TimeoutExpired/FileNotFoundError are raised by mocks
from pathlib import Path
from typing import cast

import pytest
from assertpy import assert_that

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.phpstan.phpstan_issue import PhpstanIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.core.command_builders import find_local_composer_binary
from lintro.tools.core.version_parsing import get_minimum_versions
from lintro.tools.definitions.phpstan import (
    PHPSTAN_NATIVE_CONFIGS,
    PhpstanPlugin,
    config_defines_level,
    crash_output,
)

_CLEAN_PAYLOAD = json.dumps(
    {"totals": {"errors": 0, "file_errors": 0}, "files": {}, "errors": []},
)


@pytest.fixture
def phpstan_plugin(monkeypatch: pytest.MonkeyPatch) -> PhpstanPlugin:
    """Return a fresh plugin with version checks bypassed.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        An isolated ``PhpstanPlugin`` instance.
    """
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_args, **_kwargs: None,
    )
    return PhpstanPlugin()


def _proc(
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> SubprocessResult:
    """Build a SubprocessResult for mocking ``_run_subprocess_result``.

    Args:
        stdout: Captured standard output.
        stderr: Captured standard error.
        returncode: Process exit code.

    Returns:
        SubprocessResult with the requested streams.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout,
    )


def _write_vendor_phpstan(root: Path) -> Path:
    """Create a fake ``vendor/bin/phpstan`` under *root*.

    Args:
        root: Project directory that should contain ``vendor/bin``.

    Returns:
        Path to the created executable.
    """
    vendor_bin = root / "vendor" / "bin"
    vendor_bin.mkdir(parents=True, exist_ok=True)
    binary = vendor_bin / "phpstan"
    binary.write_text("#!/bin/sh\necho local-phpstan\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


def _write_php_file(tmp_path: Path, name: str = "app.php") -> Path:
    """Write a minimal PHP file for check() discovery.

    Args:
        tmp_path: Temporary directory provided by pytest.
        name: File name to write.

    Returns:
        Path to the written file.
    """
    php = tmp_path / name
    php.write_text("<?php\necho 1;\n")
    return php


def _capture_check_cmd(
    plugin: PhpstanPlugin,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = _CLEAN_PAYLOAD,
    stderr: str = "",
    returncode: int = 0,
    side_effect: BaseException | None = None,
) -> list[list[str]]:
    """Patch ``_run_subprocess_result`` and capture analyse argv.

    Args:
        plugin: Plugin instance under test.
        monkeypatch: Pytest monkeypatch fixture.
        stdout: Stdout returned when the mock runs.
        stderr: Stderr returned when the mock runs.
        returncode: Exit code returned when the mock runs.
        side_effect: Exception to raise instead of returning a result.

    Returns:
        List that is appended with each captured command.
    """
    captured: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        timeout: int | float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: int | None = None,
    ) -> SubprocessResult:
        del timeout, cwd, env, stdin
        captured.append(list(cmd))
        if side_effect is not None:
            raise side_effect
        return _proc(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr(plugin, "_run_subprocess_result", fake_run)
    return captured


def test_phpstan_tool_definition(phpstan_plugin: PhpstanPlugin) -> None:
    """The PHPStan definition exposes the expected metadata."""
    defn = phpstan_plugin.definition
    assert_that(defn.name).is_equal_to("phpstan")
    assert_that(defn.can_fix).is_false()
    assert_that(defn.tool_type).is_equal_to(ToolType.LINTER | ToolType.TYPE_CHECKER)
    assert_that(defn.min_version).is_equal_to(get_minimum_versions()["phpstan"])
    assert_that("*.php" in defn.file_patterns).is_true()
    assert_that("phpstan.neon" in defn.native_configs).is_true()


def test_phpstan_doc_url(phpstan_plugin: PhpstanPlugin) -> None:
    """doc_url builds the error-identifier reference URL."""
    url = phpstan_plugin.doc_url("function.notFound")
    assert_that(url).is_equal_to(
        DocUrlTemplate.PHPSTAN.format(code="function.notFound"),
    )
    assert_that(phpstan_plugin.doc_url("")).is_none()


def test_phpstan_set_options_validates_level(phpstan_plugin: PhpstanPlugin) -> None:
    """set_options accepts a valid level and rejects out-of-range values."""
    phpstan_plugin.set_options(level=5)
    assert_that(phpstan_plugin.options.get("level")).is_equal_to(5)

    with pytest.raises(ValueError, match="level must be at most 9"):
        phpstan_plugin.set_options(level=10)

    plugin = PhpstanPlugin()
    with pytest.raises(ValueError, match="level must be at least 0"):
        plugin.set_options(level=-1)


def test_phpstan_build_command_adds_default_level_without_config(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
) -> None:
    """Without a native config, the command includes ``--level 0``."""
    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(tmp_path))
    level_idx = cmd.index("--level")
    assert_that(cmd[level_idx + 1]).is_equal_to("0")
    assert_that(cmd).contains("--error-format")
    assert_that(cmd).contains("a.php")
    assert_that(cmd).contains("analyse")


def test_phpstan_build_command_user_level_without_config(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
) -> None:
    """A user-set level is forwarded as ``--level 6``."""
    phpstan_plugin.set_options(level=6)
    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(tmp_path))
    level_idx = cmd.index("--level")
    assert_that(cmd[level_idx + 1]).is_equal_to("6")


@pytest.mark.parametrize("neon_name", PHPSTAN_NATIVE_CONFIGS)
def test_phpstan_build_command_omits_level_when_neon_defines_it(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
    neon_name: str,
) -> None:
    """A native neon that assigns ``level`` suppresses the injected flag.

    Args:
        phpstan_plugin: Fresh plugin fixture.
        tmp_path: Temporary directory provided by pytest.
        neon_name: Native PHPStan config file name.
    """
    (tmp_path / neon_name).write_text("parameters:\n    level: 6\n")
    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(tmp_path))
    assert_that(cmd).does_not_contain("--level")


@pytest.mark.parametrize("neon_name", PHPSTAN_NATIVE_CONFIGS)
def test_phpstan_build_command_injects_level_for_paths_only_neon(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
    neon_name: str,
) -> None:
    """A neon without ``level`` still gets the injected default ``--level 0``.

    Args:
        phpstan_plugin: Fresh plugin fixture.
        tmp_path: Temporary directory provided by pytest.
        neon_name: Native PHPStan config file name.
    """
    (tmp_path / neon_name).write_text("parameters:\n    paths:\n        - src\n")
    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(tmp_path))
    level_idx = cmd.index("--level")
    assert_that(cmd[level_idx + 1]).is_equal_to("0")


def test_phpstan_build_command_user_level_overrides_neon(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
) -> None:
    """``phpstan:level=N`` is forwarded even when the neon defines ``level``."""
    (tmp_path / "phpstan.neon").write_text("parameters:\n    level: 8\n")
    phpstan_plugin.set_options(level=6)
    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(tmp_path))
    level_idx = cmd.index("--level")
    assert_that(cmd[level_idx + 1]).is_equal_to("6")


def test_phpstan_commented_level_does_not_count_as_defined(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
) -> None:
    """A commented ``# level:`` line does not suppress injected ``--level``."""
    (tmp_path / "phpstan.neon").write_text(
        "# level: 6\nparameters:\n    paths:\n        - src\n",
    )
    assert_that(config_defines_level(tmp_path / "phpstan.neon")).is_false()
    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(tmp_path))
    assert_that(cmd).contains("--level")


def test_phpstan_explicit_configuration_without_level_injects_default(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
) -> None:
    """``--configuration`` without ``level`` still injects ``--level 0``."""
    config = tmp_path / "custom.neon"
    config.write_text("parameters:\n    paths:\n        - src\n")
    phpstan_plugin.set_options(configuration=str(config))
    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(tmp_path))
    assert_that(cmd).contains("--configuration")
    level_idx = cmd.index("--level")
    assert_that(cmd[level_idx + 1]).is_equal_to("0")


def test_local_vendor_bin_wins_over_path(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project-local ``vendor/bin/phpstan`` is preferred over PATH."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "composer.json").write_text("{}\n")
    local = _write_vendor_phpstan(project)

    path_dir = tmp_path / "pathbin"
    path_dir.mkdir()
    path_phpstan = path_dir / "phpstan"
    path_phpstan.write_text("#!/bin/sh\necho path-phpstan\n")
    path_phpstan.chmod(path_phpstan.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{path_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(project))
    assert_that(cmd[0]).is_equal_to(local.resolve().as_posix())


def test_local_vendor_bin_walks_up_from_nested_cwd(
    phpstan_plugin: PhpstanPlugin,
    tmp_path: Path,
) -> None:
    """Resolution walks up from a subdirectory to the Composer project."""
    project = tmp_path / "project"
    nested = project / "src"
    nested.mkdir(parents=True)
    (project / "composer.json").write_text("{}\n")
    local = _write_vendor_phpstan(project)

    cmd = phpstan_plugin._build_command(files=["a.php"], run_cwd=str(nested))
    assert_that(cmd[0]).is_equal_to(local.resolve().as_posix())


def test_find_local_composer_binary_ignores_decoy_above_composer_json(
    tmp_path: Path,
) -> None:
    """A ``vendor/bin`` above the nearest ``composer.json`` is not used."""
    _write_vendor_phpstan(tmp_path)
    project = tmp_path / "project"
    nested = project / "src"
    nested.mkdir(parents=True)
    (project / "composer.json").write_text("{}\n")

    found = find_local_composer_binary("phpstan", start=nested)
    assert_that(found).is_none()


def test_phpstan_check_reports_violations(
    phpstan_plugin: PhpstanPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Check parses PHPStan JSON and reports the seeded violations."""
    php = _write_php_file(tmp_path, "bad.php")
    payload = {
        "totals": {"errors": 0, "file_errors": 1},
        "files": {
            str(php): {
                "errors": 1,
                "messages": [
                    {
                        "message": "Function add invoked with 1 parameter, 2 required.",
                        "line": 2,
                        "ignorable": True,
                        "identifier": "arguments.count",
                    },
                ],
            },
        },
        "errors": [],
    }
    captured = _capture_check_cmd(
        phpstan_plugin,
        monkeypatch,
        stdout=json.dumps(payload),
        returncode=1,
    )
    result: ToolResult = phpstan_plugin.check([str(php)], {})
    assert_that(result.name).is_equal_to("phpstan")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issue = cast(PhpstanIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.identifier).is_equal_to("arguments.count")
    assert_that(captured).is_length(1)
    assert_that(captured[0]).contains("analyse")
    assert_that(captured[0]).contains("--error-format")
    assert_that(captured[0]).contains("--level")


def test_phpstan_check_clean_passes(
    phpstan_plugin: PhpstanPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A clean PHPStan run reports success with no issues."""
    php = _write_php_file(tmp_path, "good.php")
    captured = _capture_check_cmd(phpstan_plugin, monkeypatch)
    result: ToolResult = phpstan_plugin.check([str(php)], {})
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(captured[0]).contains("analyse")
    level_idx = captured[0].index("--level")
    assert_that(captured[0][level_idx + 1]).is_equal_to("0")


def test_phpstan_check_forwards_user_level(
    phpstan_plugin: PhpstanPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """check() argv includes the user-set ``--level 6``."""
    php = _write_php_file(tmp_path)
    phpstan_plugin.set_options(level=6)
    captured = _capture_check_cmd(phpstan_plugin, monkeypatch)
    phpstan_plugin.check([str(php)], {})
    level_idx = captured[0].index("--level")
    assert_that(captured[0][level_idx + 1]).is_equal_to("6")


def test_phpstan_fix_raises(phpstan_plugin: PhpstanPlugin) -> None:
    """PHPStan does not support fixing and raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        phpstan_plugin.fix(["a.php"], {})


def test_phpstan_crash_with_fatal_error_output_fails(
    phpstan_plugin: PhpstanPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A non-zero exit with unparseable stdout is a failure, not a pass."""
    php = _write_php_file(tmp_path)
    fatal = "PHP Fatal error: Allowed memory size exhausted"
    preamble = "Instructions for interpreting errors"
    _capture_check_cmd(
        phpstan_plugin,
        monkeypatch,
        stdout=fatal,
        stderr=preamble,
        returncode=255,
    )
    result: ToolResult = phpstan_plugin.check([str(php)], {})
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains(fatal)
    assert_that(result.output).contains(preamble)


def test_crash_output_joins_stderr_and_stdout() -> None:
    """Crash output keeps both streams when stderr is non-empty."""
    output = crash_output(
        stderr="guidance preamble",
        stdout="truncated json {",
    )
    assert_that(output).contains("guidance preamble")
    assert_that(output).contains("truncated json {")


def test_phpstan_timeout_output(
    phpstan_plugin: PhpstanPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A timeout returns a failure that names the timeout option."""
    php = _write_php_file(tmp_path)
    _capture_check_cmd(
        phpstan_plugin,
        monkeypatch,
        side_effect=subprocess.TimeoutExpired(cmd=["phpstan"], timeout=1),
    )
    result = phpstan_plugin.check([str(php)], {})
    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
    assert_that(result.output).contains("phpstan:timeout")


def test_phpstan_file_not_found_output(
    phpstan_plugin: PhpstanPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing binary returns install hints covering Composer and Homebrew."""
    php = _write_php_file(tmp_path)
    _capture_check_cmd(
        phpstan_plugin,
        monkeypatch,
        side_effect=FileNotFoundError("phpstan"),
    )
    result = phpstan_plugin.check([str(php)], {})
    assert_that(result.success).is_false()
    assert_that(result.output).contains("phpstan not found")
    assert_that(result.output).contains("composer require")
    assert_that(result.output).contains("brew install php phpstan")
