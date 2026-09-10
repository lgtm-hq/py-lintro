"""AI-specific exception hierarchy for Lintro.

All exceptions inherit from LintroError to maintain a consistent
exception hierarchy across the project.
"""

from __future__ import annotations

from lintro.exceptions.errors import LintroError


class AIError(LintroError):
    """Base exception for all AI-related errors."""


class AIConfigOverrideError(AIError):
    """An env-var or CLI-flag AI config override failed validation.

    Raised at config resolution so a typo'd provider or transport never
    silently falls through to the committed default. The message names the
    variable or flag and the accepted values.
    """


class AIProviderRequiredError(AIError):
    """AI is enabled but no provider was named.

    Raised by :func:`~lintro.ai.providers.get_provider` when ``ai.provider``
    is unset. The message names the three set paths (config, env, flag)
    and the accepted providers. This is a configuration error, not a
    malformed model response.
    """


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

    Attributes:
        retry_after: Seconds the provider asked the caller to wait, taken
            from the HTTP ``Retry-After`` header when the vendor sent one.
            ``None`` when the header was absent or unparseable;
            :func:`~lintro.ai.retry.with_retry` then falls back to its
            exponential backoff.
    """

    retry_after: float | None

    def __init__(self, *args: object, retry_after: float | None = None) -> None:
        """Initialize the error.

        Args:
            *args: Standard exception arguments (typically the message).
            retry_after: Parsed ``Retry-After`` value in seconds, if any.
        """
        super().__init__(*args)
        self.retry_after = retry_after


class AIProviderRegistrationError(AIError):
    """A provider plugin could not be registered or resolved.

    Base class for the registry errors raised by
    :mod:`lintro.ai.providers.registry`. This is a wiring error in lintro or a
    plugin, distinct from a vendor call failing.
    """


class AIProviderAlreadyRegisteredError(AIProviderRegistrationError):
    """Two plugins claimed the same provider name.

    Raised by :func:`~lintro.ai.providers.registry.register_provider`. Provider
    names are unique keys, so a second registration is an import-order or
    packaging bug rather than a supported override.
    """


class AIProviderNotRegisteredError(AIProviderRegistrationError):
    """No plugin is registered for the requested provider name.

    Raised by :func:`~lintro.ai.providers.registry.get_registered` both when
    the name is not an :class:`~lintro.ai.provider_enum.AIProvider` member and
    when it is one that has not registered a plugin; the message distinguishes
    the two.
    """
