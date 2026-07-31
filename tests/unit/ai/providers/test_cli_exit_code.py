"""Tests for ``CliTransport.check_exit_code`` cause resolution (#1836).

Agent CLIs frequently report a fatal error on stdout (inside their JSON
envelope) while leaving stderr empty, so the failure cause must fall back to
stdout — otherwise auth detection never fires and the user sees an empty cause.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import AIAuthenticationError, AIProviderError
from lintro.ai.providers.cli_transport import CliTransport
from tests.unit.ai.conftest import completed_process as _completed

#: The exact envelope a logged-out ``claude`` CLI writes to stdout on exit 1.
_CLAUDE_LOGGED_OUT_STDOUT = (
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"result":"Not logged in · Please run /login"}'
)


class _FakeTransport(CliTransport):
    """Minimal concrete transport for exercising exit-code mapping."""

    def parse_stdout(self, stdout: str) -> str:
        """Return stdout unchanged.

        Args:
            stdout: Raw stdout from the CLI.

        Returns:
            The unmodified stdout.
        """
        return stdout


@pytest.fixture()
def transport() -> _FakeTransport:
    """Return a transport standing in for a provider CLI.

    Returns:
        A ``_FakeTransport`` named ``claude``.
    """
    return _FakeTransport(
        binary_path="/usr/local/bin/claude",
        binary_name="claude",
        install_hint="Install the claude CLI.",
    )


def test_zero_exit_raises_nothing(transport: _FakeTransport) -> None:
    """A successful exit is not mapped to any error."""
    transport.check_exit_code(_completed(returncode=0, stdout="ok"))


@pytest.mark.parametrize(
    ("auth_patterns", "stdout"),
    [
        # anthropic (claude)
        (
            ("authentication", "login", "not logged in"),
            _CLAUDE_LOGGED_OUT_STDOUT,
        ),
        # openai (codex)
        (
            ("authentication", "login", "not authenticated"),
            "Not signed in. Please run 'codex login' to sign in with ChatGPT.",
        ),
        # cursor (cursor-agent)
        (
            ("authentication required", "login"),
            "Error: Authentication required. Run 'cursor-agent login'.",
        ),
    ],
)
def test_auth_on_stdout_with_empty_stderr_raises_auth_error(
    transport: _FakeTransport,
    auth_patterns: tuple[str, ...],
    stdout: str,
) -> None:
    """Auth wording on stdout is detected when stderr is empty."""
    result = _completed(returncode=1, stdout=stdout, stderr="")

    with pytest.raises(AIAuthenticationError) as excinfo:
        transport.check_exit_code(
            result,
            auth_patterns=auth_patterns,
            auth_hint="Run login.",
        )

    assert_that(str(excinfo.value)).contains("authentication required")
    assert_that(str(excinfo.value)).contains("Run login.")


def test_whitespace_only_stderr_falls_back_to_stdout(
    transport: _FakeTransport,
) -> None:
    """Whitespace-only stderr is treated as empty for cause resolution."""
    result = _completed(
        returncode=1,
        stdout=_CLAUDE_LOGGED_OUT_STDOUT,
        stderr="   \n\t ",
    )

    with pytest.raises(AIAuthenticationError):
        transport.check_exit_code(
            result,
            auth_patterns=("authentication", "login", "not logged in"),
        )


def test_non_auth_stdout_with_empty_stderr_surfaces_stdout(
    transport: _FakeTransport,
) -> None:
    """A non-auth stdout failure is surfaced instead of an empty cause."""
    result = _completed(
        returncode=2,
        stdout='{"is_error":true,"result":"model overloaded, try again"}',
        stderr="",
    )

    with pytest.raises(AIProviderError) as excinfo:
        transport.check_exit_code(result)

    message = str(excinfo.value)
    assert_that(message).contains("exited with code 2")
    assert_that(message).contains("model overloaded, try again")


def test_stderr_wins_over_stdout_when_both_present(
    transport: _FakeTransport,
) -> None:
    """Prefer stderr as the cause so stdout cannot pollute the message."""
    result = _completed(
        returncode=1,
        stdout='{"noise":"successful parse output"}',
        stderr="fatal: repository not found",
    )

    with pytest.raises(AIProviderError) as excinfo:
        transport.check_exit_code(result)

    message = str(excinfo.value)
    assert_that(message).contains("fatal: repository not found")
    assert_that(message).does_not_contain("successful parse output")


def test_stderr_auth_still_matches_with_noisy_stdout(
    transport: _FakeTransport,
) -> None:
    """Auth patterns still fire against stderr when stdout is non-empty."""
    result = _completed(
        returncode=1,
        stdout='{"noise":"partial output"}',
        stderr="Invalid API key provided; authentication failed",
    )

    with pytest.raises(AIAuthenticationError):
        transport.check_exit_code(result)
