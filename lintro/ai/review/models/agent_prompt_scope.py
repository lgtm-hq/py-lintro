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
