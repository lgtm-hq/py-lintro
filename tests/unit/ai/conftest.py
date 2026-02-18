"""Shared fixtures for AI tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from lintro.ai.config import AIConfig
from lintro.ai.models import AIFixSuggestion
from lintro.ai.providers.base import AIResponse, BaseAIProvider
from lintro.parsers.base_issue import BaseIssue


class MockAIProvider(BaseAIProvider):
    """Mock AI provider for testing.

    Attributes:
        responses: Queue of responses to return from complete().
        calls: Recorded calls to complete().
        _available: Whether the provider reports as available.
    """

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
        self.responses: list[AIResponse] = responses or []
        self.calls: list[dict] = []
        self._available = available
        self._call_index = 0

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "max_tokens": max_tokens,
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
        return self._available

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-model"


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
    return AIConfig(enabled=True, provider="anthropic")


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
