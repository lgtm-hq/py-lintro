"""Tests for AI secrets scanning and redaction."""

from __future__ import annotations

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


# -- scan_for_secrets: API keys -----------------------------------------------


def test_detects_api_key_assignment() -> None:
    """Detects api_key = 'some_long_value' pattern."""
    text = "api_key = 'ABCDEFGHIJKLMNOPQRST1234567890'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()
    assert_that(result[0]).contains("Potential secret detected")


def test_detects_apikey_no_separator() -> None:
    """Detects apikey= pattern without separator."""
    text = "apikey = 'ABCDEFGHIJKLMNOPQRST1234567890'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_detects_api_dash_key() -> None:
    """Detects api-key pattern."""
    text = "api-key: ABCDEFGHIJKLMNOPQRST1234567890\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


# -- scan_for_secrets: passwords -----------------------------------------------


def test_detects_password() -> None:
    """Detects password assignment."""
    text = "password = 'super_secret_password_123'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_detects_passwd() -> None:
    """Detects passwd assignment."""
    text = "passwd = 'my_password_here'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_detects_secret() -> None:
    """Detects secret assignment."""
    text = "secret = 'a_very_secret_value'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_short_password_not_detected() -> None:
    """Password shorter than 8 chars is not detected."""
    text = "password = 'short'\n"
    assert_that(scan_for_secrets(text)).is_empty()


# -- scan_for_secrets: tokens --------------------------------------------------


def test_detects_token_assignment() -> None:
    """Detects token = 'long_token_value...' pattern."""
    text = "token = 'abcdefghijklmnopqrst1234567890'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


# -- scan_for_secrets: AWS keys ------------------------------------------------


def test_detects_aws_access_key() -> None:
    """Detects AWS access key ID pattern."""
    text = "aws_access_key_id = 'AKIAIOSFODNN7EXAMPLE'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_detects_aws_secret_key() -> None:
    """Detects AWS secret access key pattern."""
    text = "aws_secret_access_key = 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


# -- scan_for_secrets: GitHub PAT ----------------------------------------------


def test_detects_github_pat() -> None:
    """Detects GitHub personal access token."""
    text = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_ghp_short_not_detected() -> None:
    """Short ghp_ prefix without enough chars is not detected."""
    text = "ghp_short\n"
    assert_that(scan_for_secrets(text)).is_empty()


# -- scan_for_secrets: OpenAI/Anthropic keys -----------------------------------


def test_detects_openai_key() -> None:
    """Detects OpenAI API key pattern."""
    text = "sk-abcdefghijklmnopqrstuvwxyz\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_sk_short_not_detected() -> None:
    """Short sk- prefix without enough chars is not detected."""
    text = "sk-short\n"
    assert_that(scan_for_secrets(text)).is_empty()


# -- scan_for_secrets: private keys --------------------------------------------


def test_detects_rsa_private_key() -> None:
    """Detect full RSA private key PEM block."""
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA...\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_detects_ec_private_key() -> None:
    """Detect full EC private key PEM block."""
    text = (
        "-----BEGIN EC PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA...\n"
        "-----END EC PRIVATE KEY-----\n"
    )
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_detects_generic_private_key() -> None:
    """Detect full generic private key PEM block."""
    text = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA...\n"
        "-----END PRIVATE KEY-----\n"
    )
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


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


# -- scan_for_secrets: case insensitivity --------------------------------------


def test_api_key_case_insensitive() -> None:
    """API_KEY (uppercase) is also detected."""
    text = "API_KEY = 'ABCDEFGHIJKLMNOPQRST1234567890'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


def test_password_case_insensitive() -> None:
    """PASSWORD (uppercase) is also detected."""
    text = "PASSWORD = 'super_secret_password_123'\n"
    result = scan_for_secrets(text)
    assert_that(result).is_not_empty()


# -- redact_secrets ------------------------------------------------------------


def test_redact_clean_text_unchanged() -> None:
    """Text without secrets is returned unchanged."""
    text = "def hello():\n    return 'world'\n"
    assert_that(redact_secrets(text)).is_equal_to(text)


def test_redact_api_key() -> None:
    """API key is replaced with [REDACTED]."""
    text = "api_key = 'ABCDEFGHIJKLMNOPQRST1234567890'\n"
    result = redact_secrets(text)
    assert_that(result).contains("[REDACTED]")
    assert_that(result).does_not_contain("ABCDEFGHIJKLMNOPQRST")


def test_redact_password() -> None:
    """Password is replaced with [REDACTED]."""
    text = "password = 'super_secret_password_123'\n"
    result = redact_secrets(text)
    assert_that(result).contains("[REDACTED]")
    assert_that(result).does_not_contain("super_secret_password_123")


def test_redact_github_pat() -> None:
    """GitHub PAT is replaced with [REDACTED]."""
    # nosemgrep: detected-github-token
    text = "token = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij\n"
    result = redact_secrets(text)
    assert_that(result).contains("[REDACTED]")
    assert_that(result).does_not_contain("ghp_ABCDEFGHIJ")


def test_redact_openai_key() -> None:
    """OpenAI key is replaced with [REDACTED]."""
    text = "key = sk-abcdefghijklmnopqrstuvwxyz\n"
    result = redact_secrets(text)
    assert_that(result).contains("[REDACTED]")
    assert_that(result).does_not_contain("sk-abcdefghij")


def test_redact_private_key() -> None:
    """Full PEM private key block is replaced with [REDACTED]."""
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA...\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    result = redact_secrets(text)
    assert_that(result).contains("[REDACTED]")
    assert_that(result).does_not_contain("BEGIN RSA PRIVATE KEY")
    assert_that(result).does_not_contain("MIIEpAIBAAKCAQEA")


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
