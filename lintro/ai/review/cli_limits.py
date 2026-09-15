"""CLI-transport size limits for large-diff review (#1967).

Context-window token budgets alone are transport-blind: a 1.5k-line PR still
fits a 200k-token window as one chunk, but the CLI path then hits wall-clock
timeouts and the 32k output-token cap. These helpers apply a tighter,
transport-aware ceiling keyed off measured diff size so the existing chunker
actually splits large CLI reviews, and recognise the output-exhaustion error
that :mod:`lintro.ai.review.chunk_split_retry` answers by splitting the chunk.
No per-call findings ceiling exists any more (lintro-ops milestone 0,
decision A).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.config import AIConfig
from lintro.ai.exceptions import AIProviderError
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = [
    "CLI_DIFF_HARD_CEILING_BYTES",
    "CLI_TRANSPORT_DIFF_TOKEN_BUDGET",
    "DiffSize",
    "assert_cli_diff_within_ceiling",
    "is_cli_output_exhaustion",
    "is_output_exhaustion_error",
    "measure_diff_size",
    "resolve_cli_diff_budget",
]

# Single source of truth for the CLI limit defaults is the AIConfig model
# (cli_max_diff_tokens / cli_max_diff_bytes); these module aliases exist for
# callers and tests that want the defaults without building a config instance.
CLI_TRANSPORT_DIFF_TOKEN_BUDGET = int(
    AIConfig.model_fields["cli_max_diff_tokens"].default,
)
CLI_DIFF_HARD_CEILING_BYTES = int(
    AIConfig.model_fields["cli_max_diff_bytes"].default,
)


@dataclass(frozen=True, slots=True)
class DiffSize:
    """Measured size of a unified diff before a CLI spawn.

    Attributes:
        lines: Newline count (plus one when the text has no trailing newline).
        bytes: UTF-8 byte length.
        tokens: Estimated tokens via the shared 4-chars-per-token heuristic.
    """

    lines: int
    bytes: int
    tokens: int


def measure_diff_size(*, unified_diff: str) -> DiffSize:
    """Measure effective diff size for transport-aware routing decisions.

    Args:
        unified_diff: Unified diff text (possibly empty).

    Returns:
        Line, byte, and estimated-token counts.
    """
    if not unified_diff:
        return DiffSize(lines=0, bytes=0, tokens=0)
    lines = unified_diff.count("\n")
    if not unified_diff.endswith("\n"):
        lines += 1
    return DiffSize(
        lines=lines,
        bytes=len(unified_diff.encode("utf-8")),
        tokens=estimate_tokens(unified_diff),
    )


def assert_cli_diff_within_ceiling(
    *,
    context: ReviewContext,
    cli_max_diff_bytes: int,
) -> None:
    """Refuse a CLI review when the full diff exceeds the hard byte ceiling.

    Args:
        context: Collected review context whose unified diff is measured.
        cli_max_diff_bytes: Absolute UTF-8 byte ceiling from AI config.

    Raises:
        ReviewContextError: When the diff is too large for a healthy CLI run.
    """
    size = measure_diff_size(unified_diff=context.unified_diff)
    if size.bytes <= cli_max_diff_bytes:
        return
    raise ReviewContextError(
        "Diff is too large for CLI-transport review "
        f"({size.bytes:,} bytes > {cli_max_diff_bytes:,} byte ceiling). "
        "Narrow with --paths, or re-run with --transport api when an API key "
        "is available.",
        code=ReviewContextErrorCode.DIFF_TOO_LARGE,
    )


def resolve_cli_diff_budget(
    *,
    context_window_budget: int,
    cli_max_diff_tokens: int,
) -> int:
    """Return the per-chunk token budget for CLI transport.

    Takes the minimum of the model context-window remainder and the CLI soft
    ceiling so large PRs route through the semantic chunker instead of one shot.

    Args:
        context_window_budget: Tokens left for diff content after prompt overhead.
        cli_max_diff_tokens: Configurable CLI soft ceiling.

    Returns:
        Positive per-chunk token budget.
    """
    return max(min(context_window_budget, cli_max_diff_tokens), 1)


def is_output_exhaustion_error(message: str) -> bool:
    """Return True when *message* looks like a mid-JSON 32k output failure.

    Args:
        message: Exception message from a CLI provider call.

    Returns:
        True when the message matches known output-cap exhaustion signatures.
    """
    # Normalize JSON spacing so needle matching is layout-independent.
    text = message.lower().replace('": "', '":"')
    # Restrictive on purpose: every Claude CLI failure envelope contains
    # generic tokens like ``is_error``, ``output_tokens`` (usage block), and
    # ``finish_reason`` — matching those would classify *any* provider error
    # (auth, timeout, 4xx) as output exhaustion and trigger the tighter-cap
    # retry on errors that a smaller response cannot fix.
    # Output-specific phrasings only: "exceeded the maximum number of
    # tokens" also matches INPUT context-window violations, and "response
    # truncated" matches generic proxy/stream errors — both would trigger a
    # tighter-cap retry that cannot help (#1967 review).
    needles = (
        'stop_reason":"max_tokens',
        'stop_reason":"length',
        'finish_reason":"length',
        "max output tokens",
        "maximum output tokens",
        "output token limit",
        "maximum number of output tokens",
    )
    return any(needle in text for needle in needles)


def is_cli_output_exhaustion(error: BaseException) -> bool:
    """Return True when *error* looks like CLI output-token exhaustion.

    Args:
        error: Exception raised by a CLI provider call.

    Returns:
        True when the error is an ``AIProviderError`` with a matching message.
    """
    if not isinstance(error, AIProviderError):
        return False
    return is_output_exhaustion_error(str(error))
