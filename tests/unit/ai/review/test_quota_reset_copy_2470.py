"""The error sticky names the usage-window reset clock (#2470).

``describe_kind`` is unit-tested alongside the classification in
``tests/unit/ai/providers/test_cli_exit_code.py``; these tests drive the
rendered comment instead, so the ``cause_text`` wiring through
``_render_error_copy`` is covered rather than assumed.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.exceptions import AIProviderError
from lintro.ai.review.github_errors import format_error_comment

#: The live envelope a session-limited ``claude`` CLI exits 1 with, as the
#: transport surfaces it (#2452 round 6).
_SESSION_LIMIT_CAUSE = (
    "claude CLI exited with code 1: "
    '{"is_error":true,"duration_api_ms":0,"total_cost_usd":0,'
    '"terminal_reason":"api_error","api_error_status":429,'
    '"result":"You\'ve hit your session limit · resets 9:40am (UTC)"}'
)


def test_error_comment_names_the_reset_time() -> None:
    """The rendered sticky tells the reader when the window reopens."""
    body = format_error_comment(
        error=AIProviderError(_SESSION_LIMIT_CAUSE),
        provider="anthropic",
    )

    assert_that(body).contains("resets at 9:40am (UTC)")


def test_reset_time_mentions_are_neutralized() -> None:
    """A mention smuggled into the reset clock cannot ping anyone."""
    cause = _SESSION_LIMIT_CAUSE.replace("9:40am (UTC)", "9am (@oncall)")

    body = format_error_comment(
        error=AIProviderError(cause),
        provider="anthropic",
    )

    assert_that(body).contains("resets at 9am (@\u200boncall)")
    assert_that(body).does_not_contain("resets at 9am (@oncall)")
