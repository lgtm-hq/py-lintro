"""Tests for OpenAI AI provider."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
)
from lintro.ai.providers import openai as mod
from lintro.ai.providers.openai import OpenAIProvider


def test_openai_provider_raises_when_sdk_missing():
    with patch.object(mod, "_has_openai", False):
        with pytest.raises(AINotAvailableError):
            OpenAIProvider()


def test_openai_provider_default_model():
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()

        assert_that(provider.model_name).is_equal_to("gpt-4o")
        assert_that(provider.name).is_equal_to("openai")


def test_openai_provider_is_available_with_no_key():
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()
        provider._api_key_env = "NONEXISTENT_KEY_VAR"

        with patch.dict("os.environ", {}, clear=True):
            assert_that(provider.is_available()).is_false()


def test_openai_provider_is_available_with_key():
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()
        provider._api_key_env = "TEST_API_KEY"

        with patch.dict(
            "os.environ",
            {"TEST_API_KEY": "sk-test"},
        ):
            assert_that(provider.is_available()).is_true()


def test_openai_provider_get_client_no_key_raises():
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()
        provider._api_key_env = "NONEXISTENT_KEY"

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(AIAuthenticationError):
                provider._get_client()
