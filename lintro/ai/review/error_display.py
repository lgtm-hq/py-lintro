"""User-facing error rendering for lintro review failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.review.exceptions import ReviewContextError, ReviewExecutionError

if TYPE_CHECKING:
    from lintro.ai.exceptions import AIError

__all__ = ["render_review_error"]


def render_review_error(
    *,
    error: AIError | ValueError,
    console: Console | None = None,
) -> None:
    """Render a review failure without a Python traceback.

    Args:
        error: The exception that aborted the review.
        console: Optional Rich console (stdout).
    """
    output = console or Console()
    title, body, hints = _format_error(error)
    text = Text()
    text.append(f"{body}\n\n", style="")
    if hints:
        text.append("Suggestions:\n", style="bold")
        for hint in hints:
            text.append(f"  • {hint}\n", style="dim")

    output.print()
    output.print(
        Panel(
            text,
            title=f"[bold red]Review failed[/bold red] — {title}",
            border_style="red",
            padding=(1, 2),
        ),
    )


def _format_error(error: AIError | ValueError) -> tuple[str, str, list[str]]:
    """Build title, body, and suggestion list for an error."""
    if isinstance(error, ReviewExecutionError):
        return _format_execution_error(error)
    if isinstance(error, ReviewContextError):
        return (
            "context collection",
            str(error),
            [
                "Verify the base branch exists (e.g. --base main)",
                "Use --uncommitted for working tree changes only",
            ],
        )
    if isinstance(error, AIAuthenticationError):
        return (
            "authentication",
            str(error),
            [
                (
                    "For Cursor: set CURSOR_API_KEY or run `agent login` "
                    "(requires agent CLI on PATH)"
                ),
                "For Anthropic/OpenAI: set the provider API key env var",
            ],
        )
    if isinstance(error, AIRateLimitError):
        return (
            "rate limit",
            str(error),
            ["Wait and retry, or switch provider/model in config"],
        )
    if isinstance(error, AIProviderError):
        body = str(error)
        hints = _provider_hints(body)
        return ("provider error", body, hints)
    if isinstance(error, ValueError):
        return (
            "invalid response",
            str(error),
            [
                "Retry the review — model output may have been malformed",
                "Try a different model via ai.model in config",
            ],
        )
    return ("unexpected error", str(error), ["Retry or check debug logs"])


def _format_execution_error(error: ReviewExecutionError) -> tuple[str, str, list[str]]:
    """Format a mid-review execution failure."""
    parts: list[str] = []
    if error.chunk_index is not None and error.total_chunks is not None:
        parts.append(
            f"Failed on chunk {error.chunk_index + 1}/{error.total_chunks}"
            + (f" during {error.step}" if error.step else ""),
        )
    if error.completed_chunks:
        noun = "chunk" if error.completed_chunks == 1 else "chunks"
        parts.append(
            f"{error.completed_chunks} {noun} completed before the failure "
            f"(results were not saved).",
        )
    if error.cause_message:
        parts.append(error.cause_message)
    elif error.message:
        parts.append(error.message)

    body = "\n".join(parts) if parts else error.message
    hints = _provider_hints(error.cause_message or error.message)
    if "timed out" in body.lower():
        hints = [
            "Increase ai.api_timeout in .lintro-config.yaml (e.g. 600.0) or use --timeout",
            "Small diffs embed inline automatically; large diffs use agentic git — allow 600s+",
            "Narrow scope with --path for large diffs",
            "Switch to anthropic/openai for faster direct API calls",
            *hints,
        ]
    title = "chunk review" if error.chunk_index is not None else "review"
    return (title, body, hints)


def _provider_hints(message: str) -> list[str]:
    """Derive provider-specific hints from an error message."""
    lowered = message.lower()
    hints: list[str] = []
    if "cursor" in lowered and ("login" in lowered or "authentication" in lowered):
        hints.append(
            "Set CURSOR_API_KEY (user or service-account key from Cursor dashboard)",
        )
    if "cursor-sdk" in lowered or "cursor sdk" in lowered:
        hints.append(
            "Ensure the agent CLI is installed: "
            "curl https://cursor.com/install -fsS | bash",
        )
    if "not found" in lowered and "agent" in lowered:
        hints.append(
            "Install the Cursor CLI: curl https://cursor.com/install -fsS | bash",
        )
    return hints
