"""Tests for scripts/ci/format-changelog.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "ci" / "format-changelog.py"


def _load_module() -> ModuleType:
    """Load format-changelog.py as an importable test module.

    Returns:
        ModuleType: The loaded module.

    Raises:
        RuntimeError: If the module spec or loader cannot be resolved.
    """
    spec = importlib.util.spec_from_file_location("format_changelog", _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    """Provide the loaded format-changelog module.

    Returns:
        ModuleType: The loaded module.
    """
    return _load_module()


def _max_line_length(text: str) -> int:
    """Return the longest line length in a document.

    Args:
        text: The document text.

    Returns:
        int: The maximum line length in characters.
    """
    return max((len(line) for line in text.splitlines()), default=0)


def test_wraps_long_list_item_within_budget(module: ModuleType) -> None:
    """A long release-note bullet is reflowed under the 88-column budget."""
    src = (
        "### Fixed\n\n"
        "- **ai/review**: provider-aware error taxonomy — surface real cause "
        "(not generic 'aborted') (#1102) (08867ca)\n"
    )
    result = module.format_changelog(src)

    assert_that(_max_line_length(result)).is_less_than_or_equal_to(module.WRAP_WIDTH)
    assert_that(result).contains("- **ai/review**:")
    # Continuation lines use a two-space hanging indent under ``- ``.
    continuation = [
        line
        for line in result.splitlines()
        if line.startswith("  ") and not line.startswith("  -")
    ]
    assert_that(continuation).is_not_empty()


def test_inline_code_span_is_not_broken(module: ModuleType) -> None:
    """Inline code spans are never split across lines, matching prettier."""
    long_code = "`ImportError: cannot import name 'x' from module 'a.very.long.path'`"
    src = f"- Issue: {long_code} when running the tool as a dependency of a project\n"
    result = module.format_changelog(src)

    assert_that(result).contains(long_code)


def test_headings_and_comments_pass_through(module: ModuleType) -> None:
    """Headings, blank lines, and HTML comments are preserved verbatim."""
    src = (
        "<!-- markdownlint-disable MD024 -->\n\n"
        "# Changelog\n\n"
        "## [1.2.3] - 2026-07-06\n"
    )
    result = module.format_changelog(src)

    assert_that(result).contains("<!-- markdownlint-disable MD024 -->")
    assert_that(result).contains("# Changelog")
    assert_that(result).contains("## [1.2.3] - 2026-07-06")


def test_is_idempotent(module: ModuleType) -> None:
    """Formatting an already-formatted document is a no-op."""
    src = (
        "- **tools**: replace repetitive tool-option type validation with "
        "schema-based checks (#1076) (caa0540)\n"
    )
    once = module.format_changelog(src)
    twice = module.format_changelog(once)

    assert_that(twice).is_equal_to(once)


def test_repository_changelog_is_already_compliant(module: ModuleType) -> None:
    """The committed CHANGELOG.md is stable under the formatter."""
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert_that(module.format_changelog(changelog)).is_equal_to(changelog)


def test_collapses_blank_line_runs(module: ModuleType) -> None:
    """Consecutive blank lines collapse to one and trail with a single newline."""
    src = "# Title\n\n\n\nParagraph text.\n\n\n"
    result = module.format_changelog(src)

    assert_that(result).is_equal_to("# Title\n\nParagraph text.\n")


def test_missing_file_is_a_non_fatal_skip(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    """A missing changelog is a warning, not a hard failure."""
    exit_code = module.main([str(tmp_path / "nope.md")])

    assert_that(exit_code).is_equal_to(0)


def test_main_formats_file_in_place(module: ModuleType, tmp_path: Path) -> None:
    """Running main rewrites an unwrapped changelog file to compliant output."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text(
        "- **scope**: a release note line that is intentionally written well "
        "beyond the eighty eight column budget to force a wrap (#1) (abc1234)\n",
        encoding="utf-8",
    )
    exit_code = module.main([str(target)])

    assert_that(exit_code).is_equal_to(0)
    assert_that(_max_line_length(target.read_text(encoding="utf-8"))).is_less_than_or_equal_to(
        module.WRAP_WIDTH,
    )
