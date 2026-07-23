# Sample file with no secrets for trufflehog testing.
# This file should pass all trufflehog scans without issues.
"""A clean module with no secrets."""

import os


def get_env_var(name: str) -> str:
    """Get environment variable safely.

    Args:
        name: The name of the environment variable.

    Returns:
        The value of the environment variable, or empty string if not set.
    """
    return os.environ.get(name, "")


def add_numbers(a: int, b: int) -> int:
    """Add two numbers.

    Args:
        a: First number.
        b: Second number.

    Returns:
        Sum of the two numbers.
    """
    return a + b
