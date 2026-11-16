"""Custom exception types for Lintro."""

from __future__ import annotations


class LintroError(Exception):
    """Base exception for all Lintro-related errors."""


class InvalidToolConfigError(LintroError):
    """Raised when a tool's configuration is invalid."""


class InvalidToolOptionError(LintroError):
    """Raised when invalid options are provided to a tool."""


class ToolExecutionError(LintroError):
    """Raised when a tool fails to execute properly."""


class ToolTimeoutError(LintroError):
    """Raised when a tool execution times out."""


class ParserError(LintroError):
    """Raised when parsing tool output fails."""


class ConfigurationError(LintroError, ValueError):
    """Raised when configuration loading or validation fails.

    Also a ``ValueError`` so existing ``except ValueError`` handlers and
    tests keep working, while CLI commands can catch this type without
    swallowing unrelated runtime ``ValueError``s.
    """


class FileAccessError(LintroError):
    """Raised when file access operations fail."""


class UnsafeCommandError(LintroError, ValueError):
    """Raised when lintro refuses to spawn a command it cannot run safely.

    Raised by :func:`lintro.plugins.subprocess_executor.validate_subprocess_command`
    before anything is spawned, for example when ``cmd.exe`` would interpret
    an argument passed to a ``.cmd`` or ``.bat`` tool (#3486). Also a
    ``ValueError`` so every existing ``except ValueError`` handler still
    catches it; the tool executor recognises it and reports the tool run as
    an execution failure rather than a verdict.
    """
