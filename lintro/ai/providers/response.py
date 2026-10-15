"""AI provider response dataclass.

Contains the ``AIResponse`` dataclass used by all AI providers to
return completion results with usage metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.reported_model import ReportedModel


@dataclass
class AIResponse:
    """Response from an AI provider API call.

    Attributes:
        content: The generated text content.
        model: Model identifier that produced this response.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.
        cost_estimate: Estimated cost in USD for this call.
        provider: Name of the provider (e.g., "anthropic", "openai").
        turns: Agent turns the transport reported for this call, when it
            reports any (the Claude CLI envelope's ``num_turns``); ``None``
            for transports that do not count turns.
        reported_models: The models the transport said answered (the Claude
            CLI envelope's ``modelUsage``); empty when it reported none
            (#2867). Distinct from ``model``, which is what was asked for.
        cost_reported: Whether ``cost_estimate`` is a figure the transport
            reported rather than one lintro priced locally (#2867).
    """

    content: str
    model: str
    input_tokens: int = field(default=0)
    output_tokens: int = field(default=0)
    cost_estimate: float = field(default=0.0)
    provider: AIProvider | str = field(default="")
    turns: int | None = field(default=None)
    reported_models: tuple[ReportedModel, ...] = field(default=())
    cost_reported: bool = field(default=False)
