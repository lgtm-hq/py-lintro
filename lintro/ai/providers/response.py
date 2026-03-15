"""AI provider response dataclass.

Contains the ``AIResponse`` dataclass used by all AI providers to
return completion results with usage metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.registry import AIProvider


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
    """

    content: str
    model: str
    input_tokens: int = field(default=0)
    output_tokens: int = field(default=0)
    cost_estimate: float = field(default=0.0)
    provider: AIProvider | str = field(default="")
