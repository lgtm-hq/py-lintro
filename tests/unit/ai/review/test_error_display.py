"""Tests for review error display."""

from __future__ import annotations

from io import StringIO

from assertpy import assert_that
from rich.console import Console

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIError,
    AIProviderError,
    AIProviderRequiredError,
)
from lintro.ai.provider_enum import AIProvider
from lintro.ai.review.error_display import render_review_error
from lintro.ai.review.exceptions import ReviewExecutionError


def test_render_timeout_includes_actionable_hints() -> None:
    """Timeout failures show chunk context and config hints."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True)
    error = ReviewExecutionError(
        message="Review aborted before all chunks completed.",
        chunk_index=5,
        total_chunks=6,
        step="reviewing",
        completed_chunks=5,
        cause_message="Cursor CLI timed out after 300s",
    )

    render_review_error(error=error, console=console)
    output = buf.getvalue()

    assert_that(output).contains("Review failed")
    assert_that(output).contains("chunk 6/6")
    assert_that(output).contains("5 chunks completed")
    assert_that(output).contains("api_timeout")
    assert_that(output).contains("inline")
    assert_that(output).does_not_contain("Traceback")


def test_render_unset_provider_is_not_invalid_response() -> None:
    """A missing provider is a configuration failure, not a parse failure."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True)

    render_review_error(
        error=AIProviderRequiredError(
            "ai.provider is required when ai.lint or ai.review is enabled. "
            "Set it via `ai.provider` in config, LINTRO_AI_PROVIDER, or --provider.",
        ),
        console=console,
    )
    output = buf.getvalue()

    assert_that(output).contains("provider unset")
    assert_that(output).contains("ai.provider")
    assert_that(output).contains("LINTRO_AI_PROVIDER")
    assert_that(output).contains("--provider")
    assert_that(output).does_not_contain("invalid response")
    assert_that(output).does_not_contain("malformed")


def test_render_provider_error_without_traceback() -> None:
    """Generic provider errors render as panels, not tracebacks."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True)

    render_review_error(
        error=AIProviderError("Cursor CLI timed out after 300s"),
        console=console,
    )
    output = buf.getvalue()

    assert_that(output).contains("Review failed")
    assert_that(output).contains("timed out")
    assert_that(output).does_not_contain("Traceback")


def _rendered(*, error: AIError | ValueError) -> str:
    """Render one error and return its console text as a single line.

    Rich hard-wraps panel bodies, so a hint that names a provider could hide a
    substring across a line break. Joining the output on whitespace makes the
    neutrality assertions below see the copy as written.

    Args:
        error: The failure to render.

    Returns:
        The rendered output with all whitespace runs collapsed to single spaces.
    """
    buf = StringIO()
    render_review_error(
        error=error,
        console=Console(file=buf, width=200, force_terminal=False),
    )
    return " ".join(buf.getvalue().split())


def _vendor_mentions(*, text: str) -> list[str]:
    """Return the vendor names *text* mentions, in any casing.

    The copy this guards is prose, so it may name a vendor title-cased
    ("For Cursor:") or upper-cased inside an env-var identifier
    ("CURSOR_API_KEY"). A case-sensitive check would pass on both, which is
    exactly the wording #2143 removed, so the match is case-insensitive.

    Args:
        text: Rendered console output.

    Returns:
        Every provider value that appears in *text*, in enum order.
    """
    lowered = text.lower()
    return [provider.value for provider in AIProvider if provider.value in lowered]


def test_vendor_guard_catches_the_historical_copy() -> None:
    """Assert the guard would have failed on the wording it replaced.

    A neutrality check nobody has seen fail may be matching nothing, so the
    pre-#2143 strings are pinned as fixtures: both the title-cased prose and
    the upper-cased env identifiers must be reported.
    """
    assert_that(
        _vendor_mentions(text="For Cursor: set CURSOR_API_KEY"),
    ).is_equal_to(["cursor"])
    assert_that(
        _vendor_mentions(text="For Anthropic/OpenAI: set ANTHROPIC_API_KEY"),
    ).contains("anthropic", "openai")
    assert_that(_vendor_mentions(text="set the API-key variable")).is_empty()


def test_auth_hints_name_no_provider() -> None:
    """The auth hints point at the configured provider, never at a vendor.

    lintro has no default provider (#2143), so a hint naming one — the copy
    used to say "For Cursor: …" and "For Anthropic/OpenAI: …" — is wrong for
    whichever providers it leaves out. The AST ratchet in
    ``tests/unit/ai/test_no_default_provider.py`` covers default-shaped
    bindings, not user-facing copy, so this is the guard for the copy.
    """
    output = _rendered(error=AIAuthenticationError("bad key"))

    assert_that(output).contains("authentication")
    assert_that(output).contains("API-key variable your provider declares")
    assert_that(_vendor_mentions(text=output)).is_empty()


def test_slow_review_hint_names_no_provider() -> None:
    """The slow-CLI hint recommends a transport, not a pair of vendors.

    It used to read "Switch to anthropic/openai for faster direct API calls",
    which both ranked providers and skipped Cursor.
    """
    output = _rendered(
        error=ReviewExecutionError(
            message="Review aborted before all chunks completed.",
            chunk_index=0,
            total_chunks=2,
            step="reviewing",
            completed_chunks=0,
            cause_message="the call timed out after 300s",
        ),
    )

    assert_that(output).contains("transport: api")
    assert_that(_vendor_mentions(text=output)).is_empty()
