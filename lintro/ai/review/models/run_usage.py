"""What one review round cost, in wall-clock time and in provider tokens."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RunUsage"]


@dataclass(frozen=True, slots=True)
class RunUsage:
    """Resource consumption measured for one AI review round.

    ``cost`` is never readable on its own: ``estimated`` says whether the
    token counts behind it were counted locally, and ``cost_basis`` says how
    the number should be read at all. Both travel with the amount so no
    renderer can present an estimate as a bill.

    Attributes:
        duration: Wall-clock duration in seconds.
        prompt: Prompt (input) tokens consumed.
        completion: Completion (output) tokens produced.
        total: Total tokens consumed.
        cost: Estimated cost in USD: everything the round charged to its
            budget (#2814), so it can exceed what the token counts price
            when a call was charged and its result dropped (a chunk
            cancelled at a budget stop, a turn-limited call).
        estimated: True when token counts were estimated locally.
        cost_basis: How ``cost`` should be read (``billed``, ``estimated``,
            or ``unpriceable``) (#1923).
        context: Prompt tokens spent on the read-only repository context
            (#2714), a subset of ``prompt``; serialized only when non-zero.
    """

    duration: float = 0.0
    prompt: int = 0
    completion: int = 0
    total: int = 0
    cost: float = 0.0
    estimated: bool = False
    cost_basis: str = ""
    context: int = 0
