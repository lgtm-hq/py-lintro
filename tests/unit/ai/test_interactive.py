"""Tests for interactive fix review."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.interactive import (
    _apply_fix,
    _group_by_code,
    review_fixes_interactive,
)
from lintro.ai.models import AIFixSuggestion


class TestApplyFix:
    """Tests for _apply_fix function."""

    def test_applies_fix(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("assert x > 0\nprint('ok')\n")

        fix = AIFixSuggestion(
            file=str(f),
            original_code="assert x > 0",
            suggested_code="if x <= 0:\n    raise ValueError",
        )

        result = _apply_fix(fix)
        assert_that(result).is_true()

        content = f.read_text()
        assert_that(content).contains("if x <= 0:")
        assert_that(content).does_not_contain("assert x > 0")

    def test_skips_when_original_not_found(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        fix = AIFixSuggestion(
            file=str(f),
            original_code="nonexistent code",
            suggested_code="new code",
        )

        result = _apply_fix(fix)
        assert_that(result).is_false()

    def test_handles_missing_file(self):
        fix = AIFixSuggestion(
            file="/nonexistent/file.py",
            original_code="x",
            suggested_code="y",
        )
        result = _apply_fix(fix)
        assert_that(result).is_false()

    def test_line_targeted_replacement(self, tmp_path):
        """Fix applies near the target line, not an earlier occurrence."""
        f = tmp_path / "test.py"
        f.write_text("x = 1\nprint('a')\nx = 1\nprint('b')\n")

        fix = AIFixSuggestion(
            file=str(f),
            line=3,
            original_code="x = 1",
            suggested_code="x = 2",
        )

        result = _apply_fix(fix)
        assert_that(result).is_true()

        content = f.read_text()
        lines = content.splitlines()
        # First occurrence should remain unchanged
        assert_that(lines[0]).is_equal_to("x = 1")
        # Third line (line 3) should be changed
        assert_that(lines[2]).is_equal_to("x = 2")

    def test_fallback_to_string_replace(self, tmp_path):
        """Falls back to first-occurrence when line targeting misses."""
        f = tmp_path / "test.py"
        f.write_text("old code\nmore stuff\n")

        fix = AIFixSuggestion(
            file=str(f),
            line=99,  # Way off — no match near this line
            original_code="old code",
            suggested_code="new code",
        )

        result = _apply_fix(fix)
        assert_that(result).is_true()
        assert_that(f.read_text()).contains("new code")

    def test_empty_original_code(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        fix = AIFixSuggestion(
            file=str(f),
            original_code="",
            suggested_code="y = 2",
        )

        result = _apply_fix(fix)
        assert_that(result).is_false()


class TestGroupByCode:
    """Tests for _group_by_code function."""

    def test_groups_by_code(self):
        fixes = [
            AIFixSuggestion(file="a.py", code="B101"),
            AIFixSuggestion(file="b.py", code="B101"),
            AIFixSuggestion(file="c.py", code="E501"),
        ]
        groups = _group_by_code(fixes)
        assert_that(groups).contains_key("B101")
        assert_that(groups).contains_key("E501")
        assert_that(groups["B101"]).is_length(2)
        assert_that(groups["E501"]).is_length(1)

    def test_empty_code_uses_unknown(self):
        fixes = [AIFixSuggestion(file="a.py", code="")]
        groups = _group_by_code(fixes)
        assert_that(groups).contains_key("unknown")

    def test_empty_list(self):
        groups = _group_by_code([])
        assert_that(groups).is_empty()


class TestReviewFixesInteractive:
    """Tests for review_fixes_interactive function."""

    def test_empty_suggestions(self):
        accepted, rejected, applied = review_fixes_interactive([])
        assert_that(accepted).is_equal_to(0)
        assert_that(rejected).is_equal_to(0)
        assert_that(applied).is_empty()

    def test_non_interactive_skips(self):
        fixes = [
            AIFixSuggestion(
                file="test.py",
                original_code="x",
                suggested_code="y",
            ),
        ]
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            accepted, rejected, applied = review_fixes_interactive(fixes)
            assert_that(accepted).is_equal_to(0)
            assert_that(applied).is_empty()
