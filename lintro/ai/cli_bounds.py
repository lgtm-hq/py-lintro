"""Per-call bounds for the agent CLIs (lintro-ops #39, #2685).

A CLI provider spawns a whole agentic session per call. Left unbounded it can
read and grep the working tree turn after turn, which in dogfood is the base
commit, not the PR, and the slowest rounds spent six figures of input tokens
on one chunk. Every call therefore carries a turn limit resolved from its
kind, and each provider declares in its metadata how (and whether) its binary
can honour it alongside a read-only tool surface.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType

from lintro.ai.enums.ai_call_kind import AICallKind

__all__ = [
    "DEFAULT_MAX_TURNS",
    "CliCallOptions",
    "bound_cli_call",
    "current_cli_call_options",
    "resolve_max_turns",
]

#: Turn limit per call kind when ``ai.transports.cli.max_turns`` is unset.
#: Review-type calls get three turns: the answer plus a little room to
#: re-read a hunk; summary and fix calls answer in one.
DEFAULT_MAX_TURNS = MappingProxyType(
    {
        AICallKind.REVIEW: 3,
        AICallKind.SUMMARY: 1,
        AICallKind.FIX: 1,
    },
)


@dataclass(frozen=True, slots=True)
class CliCallOptions:
    """Per-call options a CLI provider renders into its argv.

    Attributes:
        max_turns: Agent turn limit for this call. Providers whose bounds do
            not support a turn limit ignore it.
    """

    max_turns: int | None = None


def resolve_max_turns(*, call_kind: AICallKind, configured: int | None) -> int:
    """Return the effective turn limit for a call.

    Args:
        call_kind: What the call is for.
        configured: ``ai.transports.cli.max_turns``; an explicit value
            overrides every kind.

    Returns:
        The configured limit when set, else the kind's default.
    """
    if configured is not None:
        return configured
    return DEFAULT_MAX_TURNS[call_kind]


#: The bounds of the provider call in flight, set by ``call_ai`` for the
#: duration of one call. A context variable rather than a ``complete()``
#: keyword: the providers' ``complete`` signatures sit at the parameter
#: ratchet (PLR0913 baseline may only shrink), and the bounds are a
#: CLI-transport concern that only the CLI code paths read. Context variables
#: follow ``await`` and task boundaries, so parallel chunk calls each see
#: their own value.
_CURRENT_CALL: ContextVar[CliCallOptions | None] = ContextVar(
    "lintro_cli_call_options",
    default=None,
)


@contextmanager
def bound_cli_call(options: CliCallOptions | None) -> Iterator[None]:
    """Make *options* the bounds of the provider call issued inside the block.

    Args:
        options: The bounds to expose, or ``None`` for an unbounded call.

    Yields:
        None: The previous value is restored on exit.
    """
    token = _CURRENT_CALL.set(options)
    try:
        yield
    finally:
        _CURRENT_CALL.reset(token)


def current_cli_call_options() -> CliCallOptions | None:
    """Return the bounds of the provider call in flight, if any."""
    return _CURRENT_CALL.get()
