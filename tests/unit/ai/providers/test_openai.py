"""Tests for OpenAI AI provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
)


class TestOpenAIProvider:
    """Tests for OpenAIProvider."""

    def test_raises_when_sdk_missing(self):
        from lintro.ai.providers import openai as mod

        original = mod.openai  # type: ignore[attr-defined]
        mod.openai = None  # type: ignore[attr-defined, assignment]
        try:
            with pytest.raises(AINotAvailableError):
                mod.OpenAIProvider()
        finally:
            mod.openai = original  # type: ignore[attr-defined]

    def test_default_model(self):
        from lintro.ai.providers.openai import OpenAIProvider

        with patch.object(
            OpenAIProvider,
            "__init__",
            lambda self, **kw: None,
        ):
            provider = OpenAIProvider()
            provider._model = "gpt-4o"
            provider._api_key_env = "OPENAI_API_KEY"
            provider._max_tokens = 4096
            provider._client = None

            assert_that(provider.model_name).is_equal_to("gpt-4o")
            assert_that(provider.name).is_equal_to("openai")

    def test_is_available_with_no_key(self):
        from lintro.ai.providers.openai import OpenAIProvider

        with patch.object(
            OpenAIProvider,
            "__init__",
            lambda self, **kw: None,
        ):
            provider = OpenAIProvider()
            provider._api_key_env = "NONEXISTENT_KEY_VAR"

            import lintro.ai.providers.openai as mod

            original = mod.openai  # type: ignore[attr-defined]
            mod.openai = MagicMock()  # type: ignore[attr-defined]
            try:
                with patch.dict(
                    "os.environ",
                    {},
                    clear=True,
                ):
                    assert_that(provider.is_available()).is_false()
            finally:
                mod.openai = original  # type: ignore[attr-defined]

    def test_is_available_with_key(self):
        from lintro.ai.providers.openai import OpenAIProvider

        with patch.object(
            OpenAIProvider,
            "__init__",
            lambda self, **kw: None,
        ):
            provider = OpenAIProvider()
            provider._api_key_env = "TEST_API_KEY"

            import lintro.ai.providers.openai as mod

            original = mod.openai  # type: ignore[attr-defined]
            mod.openai = MagicMock()  # type: ignore[attr-defined]
            try:
                with patch.dict(
                    "os.environ",
                    {"TEST_API_KEY": "sk-test"},
                ):
                    assert_that(provider.is_available()).is_true()
            finally:
                mod.openai = original  # type: ignore[attr-defined]

    def test_get_client_no_key_raises(self):
        from lintro.ai.providers.openai import OpenAIProvider

        with patch.object(
            OpenAIProvider,
            "__init__",
            lambda self, **kw: None,
        ):
            provider = OpenAIProvider()
            provider._api_key_env = "NONEXISTENT_KEY"
            provider._client = None

            with patch.dict("os.environ", {}, clear=True):
                with pytest.raises(AIAuthenticationError):
                    provider._get_client()
