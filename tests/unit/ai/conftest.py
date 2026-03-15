"""Shared fixtures for AI tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import pytest

from lintro.ai.config import AIConfig
from lintro.ai.models import AIFixSuggestion
from lintro.ai.providers.base import AIResponse, BaseAIProvider
from lintro.parsers.base_issue import BaseIssue


class MockAIProvider(BaseAIProvider):
    """Thread-safe mock AI provider for testing."""

    def __init__(
        self,
        responses: list[AIResponse] | None = None,
        *,
        available: bool = True,
    ) -> None:
        """Initialize the mock AI provider.

        Args:
            responses: List of responses to return from complete() calls.
            available: Whether the provider reports as available.
        """
        super().__init__(
            provider_name="mock",
            has_sdk=True,
            sdk_package="mock",
            default_model="mock-model",
            default_api_key_env="MOCK_API_KEY",
        )
        self.responses: list[AIResponse] = responses or []
        self.calls: list[dict[str, Any]] = []
        self._available = available
        self._call_index = 0
        self._lock = threading.Lock()

    def _create_client(self, *, api_key: str) -> Any:
        """Return a mock client."""
        return None

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ) -> AIResponse:
        """Return the next queued response or a default."""
        with self._lock:
            self.calls.append(
                {
                    "prompt": prompt,
                    "system": system,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                },
            )
            if self._call_index < len(self.responses):
                response = self.responses[self._call_index]
                self._call_index += 1
                return response
        return AIResponse(
            content="{}",
            model="mock-model",
            input_tokens=10,
            output_tokens=5,
            cost_estimate=0.001,
            provider="mock",
        )

    def is_available(self) -> bool:
        """Check if the mock AI provider is available."""
        return self._available


@dataclass
class MockIssue(BaseIssue):
    """Mock issue with code and severity for testing."""

    code: str = ""
    severity: str = ""
    fixable: bool = False


@pytest.fixture
def mock_provider() -> MockAIProvider:
    """Create a mock AI provider."""
    return MockAIProvider()


@pytest.fixture
def ai_config() -> AIConfig:
    """Create a default AI config for testing."""
    return AIConfig(enabled=True, provider="anthropic")  # type: ignore[arg-type]  # Pydantic coerces str


@pytest.fixture
def ai_config_disabled() -> AIConfig:
    """Create a disabled AI config for testing."""
    return AIConfig(enabled=False)


@pytest.fixture
def sample_issues() -> list[MockIssue]:
    """Create sample issues for testing."""
    return [
        MockIssue(
            file="src/main.py",
            line=10,
            column=1,
            message="Use of assert detected",
            code="B101",
            severity="low",
        ),
        MockIssue(
            file="src/utils.py",
            line=25,
            column=5,
            message="Use of assert detected",
            code="B101",
            severity="low",
        ),
        MockIssue(
            file="src/main.py",
            line=42,
            column=1,
            message="Line too long",
            code="E501",
            severity="warning",
        ),
    ]


@pytest.fixture
def sample_fix_suggestions() -> list[AIFixSuggestion]:
    """Create sample fix suggestions for testing."""
    return [
        AIFixSuggestion(
            file="src/main.py",
            line=10,
            code="B101",
            tool_name="bandit",
            original_code="assert x > 0",
            suggested_code="if not x > 0:\n    raise ValueError",
            diff="--- a/src/main.py\n+++ b/src/main.py\n"
            "-assert x > 0\n"
            "+if not x > 0:\n"
            "+    raise ValueError",
            explanation="Replace assert with if/raise",
            confidence="high",
            input_tokens=150,
            output_tokens=80,
            cost_estimate=0.002,
        ),
    ]
