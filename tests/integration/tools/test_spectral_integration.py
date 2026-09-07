"""Integration tests for the Spectral tool definition.

These tests require the ``spectral`` CLI (``@stoplight/spectral-cli``) to be
runnable. They verify the plugin end-to-end against a minimal OpenAPI fixture
and a ``spectral:oas`` ruleset.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.spectral.spectral_issue import SpectralIssue
from lintro.tools.spectral.definition import SpectralPlugin
from tests.integration._tools import require_command
from tests.test_samples_helpers import copy_sample

# Resolve through ``SpectralPlugin._get_spectral_command`` so the gate cannot
# drift from production resolution (project-local binary, PATH, then
# bunx/npx). Probe from a neutral cwd: the tests run the plugin against tmp
# directories, and bunx/npx resolution can differ between the repo root
# (whose node_modules may satisfy the CLI) and anywhere else.
pytestmark = require_command(
    "spectral",
    SpectralPlugin()._get_spectral_command(cwd=tempfile.gettempdir()),
    cwd=tempfile.gettempdir(),
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
        "spectral_violations.yaml",
        dest_name="openapi.yaml",
    )


def test_check_detects_violations(spec_with_ruleset: Path) -> None:
    """Spectral reports findings on a spec that violates the ruleset.

    Args:
        spec_with_ruleset: OpenAPI document with a colocated ruleset.
    """
    plugin = SpectralPlugin()
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


def test_check_discovers_parent_ruleset(tmp_path: Path) -> None:
    """Real CLI execution finds a ruleset above the target document.

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
    nested = tmp_path / "specs"
    nested.mkdir()
    spec = copy_sample(
        nested,
        "tools",
        "config",
        "spectral",
        "spectral_violations.yaml",
        dest_name="openapi.yaml",
    )

    result = SpectralPlugin().check([str(spec)], {})

    assert_that(result.skipped).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


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
        "spectral_clean.yaml",
        dest_name="openapi.yaml",
    )

    plugin = SpectralPlugin()
    result = plugin.check([str(spec)], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


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
    result = plugin.check([str(spec)], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    issues = [i for i in (result.issues or []) if isinstance(i, SpectralIssue)]
    assert_that({issue.code for issue in issues}).contains("oas3-api-servers")


def test_check_detects_violations_in_yml_openapi(tmp_path: Path) -> None:
    """The ``*.yml`` pattern is exercised by the real Spectral CLI.

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
        "spectral_violations.yaml",
        dest_name="openapi.yml",
    )

    result = SpectralPlugin().check([str(spec)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


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
        "spectral_violations.yaml",
        dest_name="openapi.yaml",
    )

    plugin = SpectralPlugin()
    result = plugin.check([str(spec)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("no ruleset")
    assert_that(result.skipped).is_true()
    assert_that(result.skip_reason).contains("no ruleset")


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
        "spectral_violations.yaml",
        dest_name="openapi.yaml",
    )

    plugin = SpectralPlugin()
    result = plugin.check([str(spec)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.skipped).is_false()
    assert_that(result.timed_out).is_false()
    assert_that(result.output).is_not_empty()
    assert_that((result.output or "").lower()).contains("ruleset")
