# Sample file with fake secrets for trufflehog testing.
# WARNING: These are FAKE credentials for testing purposes only!
# This file intentionally contains patterns that trigger trufflehog detectors.
"""A module with intentional fake secrets for testing trufflehog detection."""

# GitHub PAT pattern (triggers the Github detector).
# This is a FAKE token following the ghp_ prefix format.
GITHUB_TOKEN = "ghp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8"

# AWS Access Key ID (canonical AWS docs example key, not a real credential).
# Included for documentation; trufflehog filters known example keys.
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"  # nosec B105
