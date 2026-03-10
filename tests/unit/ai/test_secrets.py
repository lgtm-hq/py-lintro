"""Tests for AI secrets scanning and redaction."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.secrets import redact_secrets, scan_for_secrets

# -- scan_for_secrets: no secrets ---------------------------------------------


def test_no_secrets_in_clean_code() -> None:
    """Clean code with no secrets returns empty list."""
    text = "def hello():\n    return 'world'\n"
    assert_that(scan_for_secrets(text)).is_empty()


def test_empty_string_no_secrets() -> None:
    """Empty string returns no secrets."""
    assert_that(scan_for_secrets("")).is_empty()


def test_normal_variable_assignments() -> None:
    """Normal variable assignments are not flagged."""
    text = "name = 'Alice'\ncount = 42\npath = '/usr/local'\n"
    assert_that(scan_for_secrets(text)).is_empty()


# -- scan_for_secrets: pattern detection (parametrized) -----------------------


@pytest.mark.parametrize(
    ("description", "text", "expected_pattern"),
    [
        (
            "api_key assignment",
            "api_key = 'ABCDEFGHIJKLMNOPQRST1234567890'\n",
            "api",
        ),
        (
            "apikey no separator",
            "apikey = 'ABCDEFGHIJKLMNOPQRST1234567890'\n",
            "api",
        ),
        (
            "api-key header",
            "api-key: ABCDEFGHIJKLMNOPQRST1234567890\n",
            "api",
        ),
        (
            "API_KEY uppercase",
            "API_KEY = 'ABCDEFGHIJKLMNOPQRST1234567890'\n",
            "api",
        ),
        (
            "password assignment",
            "password = 'super_secret_password_123'\n",
            "secret",
        ),
        (
            "PASSWORD uppercase",
            "PASSWORD = 'super_secret_password_123'\n",
            "secret",
        ),
        (
            "passwd assignment",
            "passwd = 'my_password_here'\n",
            "secret",
        ),
        (
            "secret assignment",
            "secret = 'a_very_secret_value'\n",
            "secret",
        ),
        (
            "token assignment",
            "token = 'abcdefghijklmnopqrst1234567890'\n",
            "secret",
        ),
        (
            "AWS access key ID",
            "aws_access_key_id = 'AKIAIOSFODNN7EXAMPLE'\n",
            "AWS",
        ),
        (
            "AWS secret access key",
            "aws_secret_access_key = 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'\n",
            "AWS",
        ),
        (
            "GitHub PAT",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij\n",
            "ghp_",
        ),
        (
            "OpenAI key",
            "sk-abcdefghijklmnopqrstuvwxyz\n",
            "sk-",
        ),
        (
            "RSA private key",
            (
                "-----BEGIN RSA PRIVATE KEY-----\n"
                "MIIEpAIBAAKCAQEA...\n"
                "-----END RSA PRIVATE KEY-----\n"
            ),
            "private key",
        ),
        (
            "EC private key",
            (
                "-----BEGIN EC PRIVATE KEY-----\n"
                "MIIEpAIBAAKCAQEA...\n"
                "-----END EC PRIVATE KEY-----\n"
            ),
            "private key",
        ),
        (
            "generic private key",
            (
                "-----BEGIN PRIVATE KEY-----\n"
                "MIIEpAIBAAKCAQEA...\n"
                "-----END PRIVATE KEY-----\n"
            ),
            "private key",
        ),
    ],
    ids=[
        "api_key",
        "apikey",
        "api-key",
        "API_KEY",
        "password",
        "PASSWORD",
        "passwd",
        "secret",
        "token",
        "aws-access-key",
        "aws-secret-key",
        "github-pat",
        "openai-key",
        "rsa-private-key",
        "ec-private-key",
        "generic-private-key",
    ],
)
def test_detects_secret_pattern(
    description: str,
    text: str,
    expected_pattern: str,
) -> None:
    """Detects {description} and description mentions pattern type."""
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()
    assert_that(result[0].lower()).contains(expected_pattern.lower())


# -- scan_for_secrets: negative cases -----------------------------------------


@pytest.mark.parametrize(
    ("description", "text"),
    [
        ("short password", "password = 'short'\n"),
        ("short ghp_ prefix", "ghp_short\n"),
        ("short sk- prefix", "sk-short\n"),
    ],
    ids=["short-password", "short-ghp", "short-sk"],
)
def test_does_not_detect_short_values(description: str, text: str) -> None:
    """Short values ({description}) are not flagged as secrets."""
    assert_that(scan_for_secrets(text)).is_empty()


# -- scan_for_secrets: multiple detections -------------------------------------


def test_detects_multiple_secrets() -> None:
    """Multiple different secret types are all detected."""
    text = (
        "api_key = 'ABCDEFGHIJKLMNOPQRST1234567890'\n"
        "password = 'super_secret_password_123'\n"
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij\n"
    )
    result = scan_for_secrets(text)
    assert_that(result).is_length(3)


# -- redact_secrets ------------------------------------------------------------


def test_redact_clean_text_unchanged() -> None:
    """Text without secrets is returned unchanged."""
    text = "def hello():\n    return 'world'\n"
    assert_that(redact_secrets(text)).is_equal_to(text)


@pytest.mark.parametrize(
    ("description", "text", "forbidden_substring"),
    [
        (
            "API key",
            "api_key = 'ABCDEFGHIJKLMNOPQRST1234567890'\n",
            "ABCDEFGHIJKLMNOPQRST",
        ),
        (
            "password",
            "password = 'super_secret_password_123'\n",
            "super_secret_password_123",
        ),
        (
            "GitHub PAT",
            # nosemgrep: detected-github-token
            "token = " "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij\n",
            "ghp_ABCDEFGHIJ",
        ),
        (
            "OpenAI key",
            "key = sk-abcdefghijklmnopqrstuvwxyz\n",
            "sk-abcdefghij",
        ),
        (
            "RSA private key",
            (
                "-----BEGIN RSA PRIVATE KEY-----\n"
                "MIIEpAIBAAKCAQEA...\n"
                "-----END RSA PRIVATE KEY-----\n"
            ),
            "BEGIN RSA PRIVATE KEY",
        ),
    ],
    ids=[
        "api-key",
        "password",
        "github-pat",
        "openai-key",
        "private-key",
    ],
)
def test_redact_replaces_secret(
    description: str,
    text: str,
    forbidden_substring: str,
) -> None:
    """Redaction of {description} replaces value with [REDACTED]."""
    result = redact_secrets(text)
    assert_that(result).contains("[REDACTED]")
    assert_that(result).does_not_contain(forbidden_substring)


def test_redact_multiple_secrets() -> None:
    """Multiple secrets are all redacted."""
    text = (
        "api_key = 'ABCDEFGHIJKLMNOPQRST1234567890'\n"
        "password = 'super_secret_password_123'\n"
        "normal_var = 42\n"
    )
    result = redact_secrets(text)
    assert_that(result).contains("[REDACTED]")
    assert_that(result).contains("normal_var = 42")
    assert_that(result).does_not_contain("ABCDEFGHIJKLMNOPQRST")
    assert_that(result).does_not_contain("super_secret_password_123")


def test_redact_preserves_surrounding_text() -> None:
    """Redaction preserves non-secret text around the match."""
    text = "# config\napi_key = 'ABCDEFGHIJKLMNOPQRST1234567890'\n# end\n"
    result = redact_secrets(text)
    assert_that(result).contains("# config")
    assert_that(result).contains("# end")
    assert_that(result).contains("[REDACTED]")


def test_empty_string_redact() -> None:
    """Empty string returns empty string."""
    assert_that(redact_secrets("")).is_equal_to("")
