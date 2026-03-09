"""Tests for AI fix application logic (lintro.ai.apply)."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.apply import _apply_fix, apply_fixes
from lintro.ai.models import AIFixSuggestion

# ---------------------------------------------------------------------------
# Line-targeted replacement — exact line
# ---------------------------------------------------------------------------


def test_apply_fix_exact_line_match(tmp_path):
    """Fix applies when original_code is on exactly the reported line."""
    f = tmp_path / "test.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=2,
        original_code="b = 2",
        suggested_code="b = 42",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()
    assert_that(f.read_text()).is_equal_to("a = 1\nb = 42\nc = 3\n")


# ---------------------------------------------------------------------------
# Line-targeted replacement — adjacent lines within radius
# ---------------------------------------------------------------------------


def test_apply_fix_adjacent_line_within_radius(tmp_path):
    """Fix succeeds when original_code is a few lines off the hint."""
    f = tmp_path / "test.py"
    content = "line1\nline2\ntarget\nline4\nline5\n"
    f.write_text(content)

    fix = AIFixSuggestion(
        file=str(f),
        line=5,  # hint is off by 2
        original_code="target",
        suggested_code="replaced",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()
    assert_that(f.read_text()).contains("replaced")
    assert_that(f.read_text()).does_not_contain("target")


def test_apply_fix_prefers_closest_occurrence(tmp_path):
    """When duplicate code exists, the occurrence closest to hint wins."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\nfiller\nfiller\nfiller\nx = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=5,
        original_code="x = 1",
        suggested_code="x = 99",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()

    lines = f.read_text().splitlines()
    # First occurrence untouched, last one replaced
    assert_that(lines[0]).is_equal_to("x = 1")
    assert_that(lines[4]).is_equal_to("x = 99")


# ---------------------------------------------------------------------------
# Search radius limiting
# ---------------------------------------------------------------------------


def test_apply_fix_search_radius_1_limits_search(tmp_path):
    """Radius=1 only checks target line and one line above/below."""
    f = tmp_path / "test.py"
    f.write_text("a\nb\nc\nd\ne\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=1,  # hint at line 1, target at line 5
        original_code="e",
        suggested_code="E",
    )

    result = _apply_fix(fix, workspace_root=tmp_path, search_radius=1)
    assert_that(result).is_false()
    assert_that(f.read_text()).contains("e")


def test_apply_fix_large_radius_finds_distant_match(tmp_path):
    """A large radius can reach code far from the hint."""
    lines = ["filler\n"] * 10 + ["target\n"]
    f = tmp_path / "test.py"
    f.write_text("".join(lines))

    fix = AIFixSuggestion(
        file=str(f),
        line=5,
        original_code="target",
        suggested_code="replaced",
    )

    result = _apply_fix(fix, workspace_root=tmp_path, search_radius=10)
    assert_that(result).is_true()
    assert_that(f.read_text()).contains("replaced")


# ---------------------------------------------------------------------------
# Workspace boundary enforcement
# ---------------------------------------------------------------------------


def test_apply_fix_rejects_symlink_escape(tmp_path):
    """Fix targeting a symlink that resolves outside workspace is rejected."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")

    link = workspace / "link.py"
    link.symlink_to(outside)

    fix = AIFixSuggestion(
        file=str(link),
        line=1,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=workspace)
    assert_that(result).is_false()
    assert_that(outside.read_text()).is_equal_to("x = 1\n")


def test_apply_fix_rejects_parent_traversal(tmp_path):
    """Fix with '../' traversal outside workspace is rejected."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(workspace / ".." / "outside.py"),
        line=1,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=workspace)
    assert_that(result).is_false()
    assert_that(outside.read_text()).is_equal_to("x = 1\n")


def test_apply_fix_accepts_file_inside_workspace(tmp_path):
    """Fix targeting a file inside workspace succeeds normally."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    f = workspace / "ok.py"
    f.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=1,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=workspace)
    assert_that(result).is_true()
    assert_that(f.read_text()).is_equal_to("x = 2\n")


# ---------------------------------------------------------------------------
# Empty original_code handling
# ---------------------------------------------------------------------------


def test_apply_fix_empty_original_code_returns_false(tmp_path):
    """An empty original_code causes _apply_fix to return False."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=1,
        original_code="",
        suggested_code="y = 2",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_false()
    assert_that(f.read_text()).is_equal_to("x = 1\n")


def test_apply_fix_whitespace_only_original_code(tmp_path):
    """Whitespace-only original_code does not match typical code lines."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\ny = 2\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=1,
        original_code="   ",
        suggested_code="z = 3",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_false()


# ---------------------------------------------------------------------------
# Missing file handling
# ---------------------------------------------------------------------------


def test_apply_fix_missing_file_returns_false(tmp_path):
    """_apply_fix returns False for a nonexistent file path."""
    fix = AIFixSuggestion(
        file=str(tmp_path / "nonexistent" / "file.py"),
        line=1,
        original_code="x",
        suggested_code="y",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_false()


def test_apply_fix_empty_file_path_returns_false(tmp_path):
    """_apply_fix returns False when file path is empty string."""
    fix = AIFixSuggestion(
        file="",
        line=1,
        original_code="x",
        suggested_code="y",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_false()


# ---------------------------------------------------------------------------
# Multi-line replacement
# ---------------------------------------------------------------------------


def test_apply_fix_multi_line_original_and_suggested(tmp_path):
    """Multi-line original is replaced by multi-line suggested code."""
    f = tmp_path / "test.py"
    f.write_text("if True:\n    x = 1\n    y = 2\nprint('done')\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=2,
        original_code="    x = 1\n    y = 2",
        suggested_code="    x = 10\n    y = 20\n    z = 30",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()

    content = f.read_text()
    assert_that(content).contains("x = 10")
    assert_that(content).contains("y = 20")
    assert_that(content).contains("z = 30")
    assert_that(content).does_not_contain("x = 1\n")
    assert_that(content).contains("print('done')")


def test_apply_fix_multi_line_to_single_line(tmp_path):
    """Multi-line original replaced by single line (fewer lines)."""
    f = tmp_path / "test.py"
    f.write_text("a = 1\nb = 2\nc = 3\nd = 4\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=2,
        original_code="b = 2\nc = 3",
        suggested_code="bc = 23",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()

    content = f.read_text()
    assert_that(content).contains("bc = 23")
    assert_that(content).contains("a = 1")
    assert_that(content).contains("d = 4")


def test_apply_fix_single_line_to_multi_line(tmp_path):
    """Single-line original expands to multiple lines."""
    f = tmp_path / "test.py"
    f.write_text("x = compute()\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=1,
        original_code="x = compute()",
        suggested_code="try:\n    x = compute()\nexcept Exception:\n    x = None",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()

    content = f.read_text()
    assert_that(content).contains("try:")
    assert_that(content).contains("except Exception:")


# ---------------------------------------------------------------------------
# Newline handling edge cases
# ---------------------------------------------------------------------------


def test_apply_fix_file_without_trailing_newline(tmp_path):
    """Fix works on files that do not end with a trailing newline."""
    f = tmp_path / "test.py"
    f.write_text("x = 1")  # no trailing newline

    fix = AIFixSuggestion(
        file=str(f),
        line=1,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()
    assert_that(f.read_text()).contains("x = 2")


def test_apply_fix_preserves_other_lines_newlines(tmp_path):
    """Lines not involved in the fix retain their newlines."""
    f = tmp_path / "test.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=2,
        original_code="b = 2",
        suggested_code="b = 99",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()
    assert_that(f.read_text()).is_equal_to("a = 1\nb = 99\nc = 3\n")


# ---------------------------------------------------------------------------
# Invalid / negative line numbers
# ---------------------------------------------------------------------------


def test_apply_fix_negative_line_returns_false(tmp_path):
    """Negative line number returns False."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=-1,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_false()


def test_apply_fix_line_zero_returns_false(tmp_path):
    """Line 0 means 'unspecified' — search_order is empty, returns False."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=0,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_false()


# ---------------------------------------------------------------------------
# Line number far beyond file length (clamping)
# ---------------------------------------------------------------------------


def test_apply_fix_line_beyond_file_length_clamps(tmp_path):
    """Line number beyond EOF is clamped; code near end is still found."""
    f = tmp_path / "test.py"
    f.write_text("a\nb\ntarget\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=999,
        original_code="target",
        suggested_code="replaced",
    )

    # default radius=5 should cover the 3-line file from the clamped position
    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()
    assert_that(f.read_text()).contains("replaced")


# ---------------------------------------------------------------------------
# apply_fixes — batch behaviour
# ---------------------------------------------------------------------------


def test_apply_fixes_returns_only_successful(tmp_path):
    """apply_fixes returns only successfully applied suggestions."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\ny = 2\n")

    fixes = [
        AIFixSuggestion(
            file=str(f),
            line=1,
            original_code="x = 1",
            suggested_code="x = 10",
        ),
        AIFixSuggestion(
            file=str(f),
            line=2,
            original_code="MISSING",
            suggested_code="z = 3",
        ),
    ]

    applied = apply_fixes(fixes, workspace_root=tmp_path)
    assert_that(applied).is_length(1)
    assert_that(applied[0].suggested_code).is_equal_to("x = 10")


def test_apply_fixes_empty_list(tmp_path):
    """apply_fixes with an empty list returns an empty list."""
    applied = apply_fixes([], workspace_root=tmp_path)
    assert_that(applied).is_empty()


def test_apply_fixes_all_fail(tmp_path):
    """apply_fixes returns empty when all fixes fail."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    fixes = [
        AIFixSuggestion(
            file=str(f),
            line=1,
            original_code="NOPE",
            suggested_code="y",
        ),
        AIFixSuggestion(
            file=str(f),
            line=1,
            original_code="ALSO_NOPE",
            suggested_code="z",
        ),
    ]

    applied = apply_fixes(fixes, workspace_root=tmp_path)
    assert_that(applied).is_empty()


def test_apply_fixes_forwards_search_radius(tmp_path):
    """apply_fixes passes search_radius through to _apply_fix."""
    f = tmp_path / "test.py"
    lines = ["filler\n"] * 20 + ["target\n"]
    f.write_text("".join(lines))

    fixes = [
        AIFixSuggestion(
            file=str(f),
            line=1,
            original_code="target",
            suggested_code="replaced",
        ),
    ]

    # radius=2 won't reach line 21 from line 1
    applied = apply_fixes(fixes, workspace_root=tmp_path, search_radius=2)
    assert_that(applied).is_empty()
    assert_that(f.read_text()).contains("target")


def test_apply_fixes_forwards_auto_apply(tmp_path):
    """apply_fixes passes auto_apply through to _apply_fix."""
    f = tmp_path / "test.py"
    f.write_text("old code\nline 2\n")

    fixes = [
        AIFixSuggestion(
            file=str(f),
            line=1,
            original_code="old code",
            suggested_code="new code",
        ),
    ]

    with patch("lintro.ai.apply._apply_fix", return_value=True) as mock:
        apply_fixes(fixes, auto_apply=True, workspace_root=tmp_path)
        mock.assert_called_once()
        assert_that(mock.call_args.kwargs["auto_apply"]).is_true()


# ---------------------------------------------------------------------------
# Logging behaviour
# ---------------------------------------------------------------------------


@patch("lintro.ai.apply.logger")
def test_apply_fix_logs_debug_for_invalid_line(mock_logger, tmp_path):
    """Invalid (non-int-like) line triggers a debug log, not a crash."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=-5,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_false()
    mock_logger.debug.assert_called_once()


@patch("lintro.ai.apply.logger")
def test_apply_fix_successful_no_warning(mock_logger, tmp_path):
    """Successful line-targeted replacement logs no warning."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=1,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=tmp_path)
    assert_that(result).is_true()
    mock_logger.warning.assert_not_called()
