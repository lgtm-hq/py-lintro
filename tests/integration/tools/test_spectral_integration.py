"""Integration tests for the Spectral tool definition.

These tests require the ``spectral`` CLI (``@stoplight/spectral-cli``) to be
runnable. They verify the plugin end-to-end against a minimal OpenAPI fixture
and a ``spectral:oas`` ruleset.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.spectral.spectral_issue import SpectralIssue
from lintro.tools.definitions.spectral import SpectralPlugin

RULESET = 'extends: ["spectral:oas"]\n'
OPENAPI_WITH_ISSUES = """\
openapi: 3.0.0
info:
  title: Sample API
  version: 1.0.0
paths:
  /users:
    get:
      responses:
        '200':
          description: OK
"""
OPENAPI_CLEAN = """\
openapi: 3.0.0
info:
  title: Sample API
  version: 1.0.0
  description: A clean sample API used for linting tests.
  contact:
    name: API Team
    url: https://example.com
    email: api@example.com
  license:
    name: MIT
    url: https://opensource.org/licenses/MIT
servers:
  - url: https://api.example.com
tags:
  - name: users
    description: User operations
paths:
  /users:
    get:
      operationId: listUsers
      description: List all users.
      tags:
        - users
      responses:
        '200':
          description: OK
"""


def spectral_command() -> list[str] | None:
    """Resolve a runnable spectral command.

    Returns:
        A command prefix list if spectral can run, otherwise None.
    """
    candidates: list[list[str]] = []
    if shutil.which("spectral"):
        candidates.append(["spectral"])
    if shutil.which("bunx"):
        candidates.append(["bunx", "spectral"])
    if shutil.which("npx"):
        candidates.append(["npx", "--yes", "@stoplight/spectral-cli"])
    for cmd in candidates:
        try:
            result = subprocess.run(
                [*cmd, "--version"],
                capture_output=True,
                timeout=60,
                check=False,
            )
            if result.returncode == 0:
                return cmd
        except (subprocess.TimeoutExpired, OSError):
            continue
    return None


pytestmark = pytest.mark.skipif(
    spectral_command() is None,
    reason="spectral CLI not available",
)


@pytest.fixture
def spec_with_ruleset(tmp_path: Path) -> Path:
    """Create an OpenAPI spec with violations plus a ruleset.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the created OpenAPI document.
    """
    (tmp_path / ".spectral.yaml").write_text(RULESET)
    spec = tmp_path / "openapi.yaml"
    spec.write_text(OPENAPI_WITH_ISSUES)
    return spec


def test_check_detects_violations(spec_with_ruleset: Path) -> None:
    """Spectral reports findings on a spec that violates the ruleset.

    Args:
        spec_with_ruleset: OpenAPI document with a colocated ruleset.
    """
    plugin = SpectralPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(spec_with_ruleset)], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)

    issue = result.issues[0]
    if not isinstance(issue, SpectralIssue):
        pytest.fail("issue should be a SpectralIssue")
    assert_that(issue.code).is_not_empty()
    assert_that(issue.line).is_greater_than(0)


def test_check_clean_spec_passes(tmp_path: Path) -> None:
    """Spectral passes on a spec that satisfies the ruleset.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / ".spectral.yaml").write_text(RULESET)
    spec = tmp_path / "openapi.yaml"
    spec.write_text(OPENAPI_CLEAN)

    plugin = SpectralPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(spec)], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_skips_without_ruleset(tmp_path: Path) -> None:
    """Spectral skips gracefully when no ruleset is present.

    Args:
        tmp_path: Pytest temporary directory.
    """
    spec = tmp_path / "openapi.yaml"
    spec.write_text(OPENAPI_WITH_ISSUES)

    plugin = SpectralPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(spec)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("no ruleset")
