"""AI-specific exception hierarchy for Lintro.

All exceptions inherit from LintroError to maintain a consistent
exception hierarchy across the project.
"""

from __future__ import annotations

from lintro.exceptions.errors import LintroError


class AIError(LintroError):
    """Base exception for all AI-related errors."""


class AICostBudgetExceededError(AIError):
    """The configured AI cost budget (``ai.max_cost_usd``) was reached.

    Raised by :class:`~lintro.ai.budget.CostBudget` when cumulative spend meets
    or exceeds the ceiling. This is an *expected* graceful stop, not a provider
    failure: the review orchestrator catches it to finalize a partial review of
    the chunks completed so far rather than surfacing it as an error.
    """


class AINotAvailableError(AIError):
    """AI dependencies are not installed.

    Raised when AI features are requested but the required packages
    (anthropic, openai) are not available. The error message includes
    installation instructions.
    """


class AIProviderError(AIError):
    """Error communicating with an AI provider.

    Raised for general API communication failures such as network
    errors, server errors, or unexpected response formats.
    """


class AIAuthenticationError(AIProviderError):
    """API key is invalid or missing.

    Raised when the provider rejects the API key or when no API key
    can be found in the expected environment variable.
    """


class AIRateLimitError(AIProviderError):
    """Rate limit exceeded on the AI provider.

    Raised when the provider returns a rate limit error. Users should
    wait and retry, or switch to a different provider/model.
    """
