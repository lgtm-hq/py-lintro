"""Integration test for prettier fmt convergence with proseWrap.

Verifies that lintro's fix retry logic handles prettier's non-idempotent
``proseWrap: "always"`` behavior on programmatically-generated markdown
content (the pattern that triggers the lgtm-ci release workflow failure).
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.definitions.prettier import PrettierPlugin
from lintro.utils.tool_executor import _run_fix_with_retry

# Markdown content that triggers prettier non-idempotency with proseWrap: always.
# Long lines with inline links and mixed punctuation can cause prettier to
# re-wrap differently on a second pass.
PROBLEMATIC_CHANGELOG = """\
# Changelog

## [0.52.4](https://github.com/example/repo/compare/v0.52.3...v0.52.4) (2026-02-24)

### Bug Fixes

* **ci:** publish semver-tagged Docker images on release ([#637](https://github.com/example/repo/issues/637)) ([850de62](https://github.com/example/repo/commit/850de6200000000000000000000000000000dead))
* **ci:** use full 40-char SHA for immutable Docker image tags to comply with SLSA provenance requirements and sigstore verification constraints ([#639](https://github.com/example/repo/issues/639)) ([1950030](https://github.com/example/repo/commit/195003000000000000000000000000000000dead))
* **ruff:** pass incremental and tool_name to file discovery so that incremental mode and tool-specific file patterns are respected during ruff check and fix operations ([#629](https://github.com/example/repo/issues/629)) ([83ac42d](https://github.com/example/repo/commit/83ac42d00000000000000000000000000000dead))
"""


@pytest.fixture
def changelog_project() -> Generator[Path, None, None]:
    """Create a temp project with problematic CHANGELOG.md content.

    Yields:
        Path: Path to the temporary project directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        (project / "CHANGELOG.md").write_text(PROBLEMATIC_CHANGELOG)
        yield project


@pytest.fixture
def prettier_plugin(
    skip_if_tool_unavailable: Callable[[str], None],
    lintro_test_mode: str,
) -> PrettierPlugin:
    """Provide a PrettierPlugin instance, skipping if prettier unavailable.

    Args:
        skip_if_tool_unavailable: Fixture to skip if prettier is not installed.
        lintro_test_mode: Fixture that sets LINTRO_TEST_MODE=1.

    Returns:
        A PrettierPlugin instance.
    """
    skip_if_tool_unavailable("prettier")
    return PrettierPlugin()


@pytest.mark.prettier
def test_prettier_fmt_converges_on_changelog(
    changelog_project: Path,
    prettier_plugin: PrettierPlugin,
) -> None:
    """Verify that lintro fmt converges on non-idempotent markdown content.

    This test reproduces the lgtm-ci release failure where prettier --write
    followed by prettier --check reports remaining issues because
    ``proseWrap: "always"`` re-wraps content differently on each pass.

    The fix retry logic should make this converge within max_fix_retries.

    Args:
        changelog_project: Temp dir with problematic CHANGELOG.md.
        prettier_plugin: PrettierPlugin instance.
    """
    changelog = str(changelog_project / "CHANGELOG.md")

    # Run single pass first to establish baseline — may or may not converge
    # depending on prettier version and content specifics
    single_pass_result = _run_fix_with_retry(
        tool=prettier_plugin,
        paths=[changelog],
        options={},
        max_retries=1,
    )
    single_remaining = single_pass_result.remaining_issues_count or 0

    # Reset file to original content for the multi-pass test
    (changelog_project / "CHANGELOG.md").write_text(PROBLEMATIC_CHANGELOG)

    retry_result = _run_fix_with_retry(
        tool=prettier_plugin,
        paths=[changelog],
        options={},
        max_retries=3,
    )

    # With retry, the result should converge (0 remaining issues)
    retry_remaining = retry_result.remaining_issues_count or 0
    assert_that(retry_remaining).is_equal_to(0)
    assert_that(retry_result.success).is_true()

    # Retry should do at least as well as single pass
    assert_that(retry_remaining).is_less_than_or_equal_to(single_remaining)

    # Verify the file is stable: running check again should find no issues
    final_check = prettier_plugin.check([changelog], {})
    assert_that(final_check.success).is_true()
    assert_that(final_check.issues_count).is_equal_to(0)


@pytest.mark.prettier
def test_prettier_fmt_stable_after_convergence(
    changelog_project: Path,
    prettier_plugin: PrettierPlugin,
) -> None:
    """Verify that prettier output is stable after convergence.

    After fix with retry converges, running fix again should be a no-op.

    Args:
        changelog_project: Temp dir with problematic CHANGELOG.md.
        prettier_plugin: PrettierPlugin instance.
    """
    changelog = str(changelog_project / "CHANGELOG.md")

    # Converge first
    converge_result = _run_fix_with_retry(
        tool=prettier_plugin,
        paths=[changelog],
        options={},
        max_retries=3,
    )
    assert_that(converge_result.success).is_true()

    # Capture content after convergence
    converged_content = (changelog_project / "CHANGELOG.md").read_text()

    # Run fix again — content should not change
    second_result = prettier_plugin.fix([changelog], {})
    after_second_content = (changelog_project / "CHANGELOG.md").read_text()

    assert_that(after_second_content).is_equal_to(converged_content)
    second_remaining = second_result.remaining_issues_count or 0
    assert_that(second_remaining).is_equal_to(0)
