"""Tests for markdown doc fixer used by the documentation site pipeline."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[4]
FIX_SCRIPT = ROOT / "scripts" / "ci" / "site" / "fix-markdown-docs.py"


def _load_fix_module() -> Any:
    """Load fix-markdown-docs.py as a module.

    Returns:
        Imported module object.
    """
    spec = importlib.util.spec_from_file_location(
        "fix_markdown_docs",
        FIX_SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fix_heading_levels_skips_fenced_code_comments() -> None:
    """Hash comments inside fences must not become headings or skew levels."""
    mod = _load_fix_module()
    lines = [
        "# Title",
        "",
        "```bash",
        "# Configuration:",
        "echo hi",
        "```",
        "",
        "### Too deep",
    ]
    fixed = mod.fix_heading_levels(lines)
    assert_that(fixed).contains("# Configuration:")
    assert_that(fixed).contains("## Too deep")


def test_wrap_prose_preserves_blockquote_marker() -> None:
    """Blockquote lines should not be rewrapped without the leading marker."""
    mod = _load_fix_module()
    quote = (
        "> This is a long blockquote line that would otherwise be wrapped by the "
        "prose wrapper and lose its markdown blockquote marker if treated as plain "
        "paragraph text without special handling for the greater-than prefix."
    )
    fixed = mod.wrap_prose_lines([quote])
    assert_that(fixed).is_length(1)
    assert_that(fixed[0]).starts_with("> ")
