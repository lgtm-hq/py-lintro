"""Scope descriptor for generated AI-agent remediation prompts."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.agent_prompt_scope_kind import AgentPromptScopeKind

__all__ = ["AgentPromptScope"]


@dataclass(frozen=True, slots=True)
class AgentPromptScope:
    """Describes which finding set a rendered agent prompt covers.

    Attributes:
        kind: Which finding set the prompt covers.
        round_number: One-based review round the prompt was generated for, or
            ``None`` when the surface does not track rounds. Used to render
            "rounds 1-N" / "round N" wording in the scope sentence.
    """

    kind: AgentPromptScopeKind
    round_number: int | None = None

    def __post_init__(self) -> None:
        """Reject round numbers that cannot describe a real review round.

        Raises:
            ValueError: When ``round_number`` is set but not a positive,
                one-based round.
        """
        if self.round_number is not None and self.round_number < 1:
            raise ValueError(
                f"round_number must be >= 1 when set, got {self.round_number}",
            )
