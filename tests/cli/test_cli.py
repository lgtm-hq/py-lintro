"""Tests for CLI module."""

import os
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import sys
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli

_ASCII_LOCALE = "en_US.US-ASCII"


def _scrubbed_ascii_env() -> dict[str, str]:
    """Build a brew-test-like env that forces ASCII stdio encoding.

    Returns:
        Minimal environment with ``LC_ALL`` set to an ASCII locale.
    """
    path = os.environ.get("PATH", "/usr/bin:/bin")
    home = os.environ.get("HOME", "/tmp")
    env: dict[str, str] = {
        "PATH": path,
        "HOME": home,
        "LC_ALL": _ASCII_LOCALE,
        "LANG": _ASCII_LOCALE,
    }
    # Preserve vars needed to find the project venv / uv cache when present.
    for key in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_CACHE_DIR", "TMPDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _ascii_locale_forces_ascii_stdio() -> bool:
    """Return whether ``en_US.US-ASCII`` makes CPython use ASCII stdio.

    Returns:
        ``True`` when a subprocess under that locale reports ASCII encoding.
    """
    probe = subprocess.run(  # nosec B603 - fixed argv; encoding probe only
        [
            sys.executable,
            "-c",
            "import sys; print(sys.stdout.encoding or '')",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=_scrubbed_ascii_env(),
        check=False,
    )
    encoding = probe.stdout.strip().lower().replace("-", "")
    return probe.returncode == 0 and encoding in {"ascii", "usascii"}


def test_cli_help() -> None:
    """Test that CLI shows help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Lintro")


def _assert_help_succeeds_under_ascii_stdio(*, env: dict[str, str]) -> None:
    """Assert ``python -m lintro --help`` succeeds with ASCII stdio.

    Args:
        env: Subprocess environment that forces ASCII stdout encoding.
    """
    result = (
        subprocess.run(  # nosec B603 - fixed argv run against project CLI; shell=False
            [sys.executable, "-m", "lintro", "--help"],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            env=env,
            check=False,
        )
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert_that(combined).does_not_contain("UnicodeEncodeError")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Lintro")
    assert_that(result.stdout).contains("🔧")


def test_cli_help_succeeds_under_ascii_locale() -> None:
    """Regression #1379: --help must not UnicodeEncodeError under ASCII locales.

    Mirrors brew-test's scrubbed environment (``env -i`` + ``LC_ALL`` ASCII).
    Skips when the host lacks an effective ``en_US.US-ASCII`` locale.
    """
    if not _ascii_locale_forces_ascii_stdio():
        pytest.skip(f"Locale {_ASCII_LOCALE} does not force ASCII stdio on this host")

    _assert_help_succeeds_under_ascii_stdio(env=_scrubbed_ascii_env())


def test_cli_help_succeeds_with_ascii_pythonioencoding() -> None:
    """Regression #1379: --help survives PYTHONIOENCODING=ascii (portable).

    Covers hosts without ``en_US.US-ASCII`` while still forcing ASCII stdio
    the same way a non-UTF-8 locale would for the CPython package entry point.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "ascii"
    env.pop("PYTHONUTF8", None)
    _assert_help_succeeds_under_ascii_stdio(env=env)


def test_cli_version() -> None:
    """Test that CLI shows version."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.lower()).contains("version")


@pytest.mark.parametrize(
    "command",
    ["check", "format", "list-tools", "test"],
    ids=["check", "format", "list-tools", "test"],
)
def test_cli_commands_registered(command: str) -> None:
    """Test that all commands are registered and show help.

    Args:
        command: CLI command to test.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [command, "--help"])
    assert_that(result.exit_code).is_equal_to(0)


def test_main_function() -> None:
    """Test the main function."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Lintro")


@pytest.mark.parametrize(
    "alias,expected_text",
    [
        ("chk", "check"),
        ("fmt", "format"),
        ("ls", "list all available tools"),
        ("tst", "Run tests"),
    ],
    ids=["chk", "fmt", "ls", "tst"],
)
def test_cli_command_aliases(alias: str, expected_text: str) -> None:
    """Test that command aliases work.

    Args:
        alias: Command alias to test.
        expected_text: Text expected in help output.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [alias, "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.lower()).contains(expected_text.lower())


def test_cli_with_no_args() -> None:
    """Test CLI with no arguments."""
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).is_equal_to("")


def test_main_module_execution() -> None:
    """Test that __main__.py can be executed directly."""
    with patch.object(sys, "argv", ["lintro", "--help"]):
        import lintro.__main__

        assert_that(lintro.__main__).is_not_none()


def test_main_module_as_script() -> None:
    """Test that __main__.py works when run as a script."""
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [sys.executable, "-m", "lintro", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Lintro")


def test_command_chaining_basic() -> None:
    """Test basic command chaining syntax recognition."""
    runner = CliRunner()
    # Patch both format and check commands to prevent real tools from executing
    with (
        patch("lintro.cli_utils.commands.format.run_lint_tools_simple") as mock_fmt,
        patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_chk,
    ):
        mock_fmt.return_value = 0
        mock_chk.return_value = 0
        # Test that chaining syntax is accepted (should parse correctly)
        result = runner.invoke(cli, ["fmt", ",", "chk"])
        # We expect this to succeed with mocked runners, not parsing errors
        assert_that(result.output).does_not_contain("Error: unexpected argument")


@pytest.mark.parametrize(
    "command",
    ["check", "format"],
    ids=["check", "format"],
)
def test_pytest_excluded_from_command_help(command: str) -> None:
    """Test that pytest is excluded from available tools in check/format commands.

    Args:
        command: CLI command to test.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [command, "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    # The help should not mention pytest as an available tool
    assert_that(result.output).does_not_contain("pytest")
