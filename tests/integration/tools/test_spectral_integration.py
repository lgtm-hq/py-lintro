"""Integration tests for the Spectral tool definition.

These tests require the ``spectral`` CLI (``@stoplight/spectral-cli``) to be
runnable. They verify the plugin end-to-end against a minimal OpenAPI fixture
and a ``spectral:oas`` ruleset.
"""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.spectral.spectral_issue import SpectralIssue
from lintro.tools.definitions.spectral import SpectralPlugin
from tests.test_samples_helpers import copy_sample


def spectral_command() -> list[str] | None:
    """Resolve the command the plugin itself would run, if it works.

    Uses ``SpectralPlugin._get_spectral_command`` so the probe cannot drift
    from production resolution (project-local binary, PATH, then bunx/npx).

    Returns:
        The plugin's command prefix if it runs, otherwise None.
    """
    plugin = SpectralPlugin()
    cmd = plugin._get_spectral_command(cwd=tempfile.gettempdir())
    try:
        # Probe from a neutral cwd: the tests run the plugin against tmp
        # directories, and bunx/npx resolution can differ between the repo
        # root (whose node_modules may satisfy the CLI's dependencies) and
        # anywhere else. Probing from the repo would validate an invocation
        # that then fails inside the tests.
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            [*cmd, "--version"],
            capture_output=True,
            timeout=60,
            check=False,
            cwd=tempfile.gettempdir(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return cmd if result.returncode == 0 else None


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
    copy_sample(
        tmp_path,
        "tools",
        "config",
        "spectral",
        ".spectral.yaml",
    )
    return copy_sample(
        tmp_path,
        "tools",
        "config",
        "spectral",
        "openapi_violations.yaml",
        dest_name="openapi.yaml",
    )


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

    issues = [i for i in (result.issues or []) if isinstance(i, SpectralIssue)]
    if not issues:
        pytest.fail("expected at least one SpectralIssue")
    codes = {issue.code for issue in issues}
    assert_that(codes).contains("operation-operationId")
    issue = next(issue for issue in issues if issue.code == "operation-operationId")
    assert_that(issue.path).contains("paths./users.get")
    assert_that(issue.line).is_greater_than(0)


def test_check_clean_spec_passes(tmp_path: Path) -> None:
    """Spectral passes on a spec that satisfies the ruleset.

    Args:
        tmp_path: Pytest temporary directory.
    """
    copy_sample(
        tmp_path,
        "tools",
        "config",
        "spectral",
        ".spectral.yaml",
    )
    spec = copy_sample(
        tmp_path,
        "tools",
        "config",
        "spectral",
        "openapi_clean.yaml",
        dest_name="openapi.yaml",
    )

    plugin = SpectralPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(spec)], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_detects_violations_in_json_openapi(tmp_path: Path) -> None:
    """JSON OpenAPI documents are in Spectral's file patterns, not only YAML.

    Args:
        tmp_path: Pytest temporary directory.
    """
    copy_sample(
        tmp_path,
        "tools",
        "config",
        "spectral",
        ".spectral.yaml",
    )
    spec = tmp_path / "openapi.json"
    spec.write_text(
        '{"openapi":"3.0.0","info":{"title":"S","version":"1.0.0"},"paths":{}}',
    )

    plugin = SpectralPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(spec)], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    issues = [i for i in (result.issues or []) if isinstance(i, SpectralIssue)]
    assert_that({issue.code for issue in issues}).contains("oas3-api-servers")


def test_check_skips_without_ruleset(tmp_path: Path) -> None:
    """Spectral skips gracefully when no ruleset is present.

    Args:
        tmp_path: Pytest temporary directory.
    """
    spec = copy_sample(
        tmp_path,
        "tools",
        "config",
        "spectral",
        "openapi_violations.yaml",
        dest_name="openapi.yaml",
    )

    plugin = SpectralPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(spec)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("no ruleset")


def test_invalid_ruleset_is_not_reported_as_clean(tmp_path: Path) -> None:
    """A real Spectral ruleset error fails closed instead of passing.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / ".spectral.yaml").write_text("extends: [\n")
    spec = copy_sample(
        tmp_path,
        "tools",
        "config",
        "spectral",
        "openapi_violations.yaml",
        dest_name="openapi.yaml",
    )

    plugin = SpectralPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(spec)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_not_empty()
