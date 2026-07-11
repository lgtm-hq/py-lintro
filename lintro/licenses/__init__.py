"""Dependency license compliance checking for Lintro.

This package scans project dependencies, detects their licenses, normalizes
them to SPDX identifiers, and validates them against a configurable
allow/deny policy. See :mod:`lintro.cli_utils.commands.licenses` for the CLI
entry point.

The implementation is intentionally self-contained so it can later be unified
with a shared dependency-policy module (see issue #481, version policy)
without a hard dependency today.
"""

from lintro.licenses.models import (
    LicenseResult,
    LicenseStatus,
    PackageLicense,
)

__all__ = [
    "LicenseResult",
    "LicenseStatus",
    "PackageLicense",
]
