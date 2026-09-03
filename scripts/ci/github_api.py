#!/usr/bin/env python3
"""Run authenticated GitHub CLI API requests for CI helper scripts."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - fixed gh executable and flags


def gh_json(*args: str) -> object:
    """Run a fixed ``gh api`` command and decode its JSON response.

    Args:
        *args: Arguments passed through to ``gh api``.

    Raises:
        RuntimeError: If the GitHub CLI exits unsuccessfully.

    Returns:
        The decoded GitHub API response.
    """
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    result = subprocess.run(  # nosec B603, B607 - gh argv, shell=False
        ["gh", "api", *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "GH_TOKEN": token},
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh api failed")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)
