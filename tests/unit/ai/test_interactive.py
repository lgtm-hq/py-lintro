"""Tests for interactive fix review."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.apply import _apply_fix, apply_fixes
from lintro.ai.interactive import (
    _group_by_code,
    _render_prompt,
    review_fixes_interactive,
)
from lintro.ai.models import AIFixSuggestion

# -- _apply_fix fallback warning ----------------------------------------------


@patch("lintro.ai.apply.logger")
def test_apply_fix_fallback_logs_debug(mock_logger, tmp_path):
    """Falling back to str.replace should log at debug level."""
    f = tmp_path / "test.py"
    f.write_text("old code\nmore stuff\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=99,  # Way off -- no match near this line
        original_code="old code",
        suggested_code="new code",
    )

    result = _apply_fix(fix)
    assert_that(result).is_true()
    mock_logger.debug.assert_called_once()
    call_args = mock_logger.debug.call_args[0][0]
    assert_that(call_args).contains("falling back")
    assert_that(call_args).contains(str(f))


@patch("lintro.ai.apply.logger")
def test_apply_fix_line_targeted_does_not_log_warning(mock_logger, tmp_path):
    """Successful line-targeted replacement should NOT log a warning."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\nprint('ok')\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=1,
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix)
    assert_that(result).is_true()
    mock_logger.warning.assert_not_called()


# -- _apply_fix ----------------------------------------------------------------


def test_apply_fix_applies_fix(tmp_path):
    """Verify that a valid fix replaces the original code in the file."""
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


def test_apply_fix_skips_when_original_not_found(tmp_path):
    """_apply_fix returns False when original code is not found."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        original_code="nonexistent code",
        suggested_code="new code",
    )

    result = _apply_fix(fix)
    assert_that(result).is_false()


def test_apply_fix_handles_missing_file():
    """Verify that _apply_fix returns False for a nonexistent file path."""
    fix = AIFixSuggestion(
        file="/nonexistent/file.py",
        original_code="x",
        suggested_code="y",
    )
    result = _apply_fix(fix)
    assert_that(result).is_false()


def test_apply_fix_line_targeted_replacement(tmp_path):
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


def test_apply_fix_fallback_to_string_replace(tmp_path):
    """Falls back to first-occurrence when line targeting misses."""
    f = tmp_path / "test.py"
    f.write_text("old code\nmore stuff\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=99,  # Way off -- no match near this line
        original_code="old code",
        suggested_code="new code",
    )

    result = _apply_fix(fix)
    assert_that(result).is_true()
    assert_that(f.read_text()).contains("new code")


def test_apply_fix_empty_original_code(tmp_path):
    """Verify that an empty original_code string causes _apply_fix to return False."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    fix = AIFixSuggestion(
        file=str(f),
        original_code="",
        suggested_code="y = 2",
    )

    result = _apply_fix(fix)
    assert_that(result).is_false()


def test_apply_fix_blocks_writes_outside_workspace_root(tmp_path):
    """Verify that fixes targeting files outside workspace_root are rejected."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("x = 1\n", encoding="utf-8")

    fix = AIFixSuggestion(
        file=str(outside_file),
        original_code="x = 1",
        suggested_code="x = 2",
    )

    result = _apply_fix(fix, workspace_root=workspace_root)
    assert_that(result).is_false()
    assert_that(outside_file.read_text(encoding="utf-8")).is_equal_to("x = 1\n")


def test_apply_fix_auto_apply_skips_fallback(tmp_path):
    """When auto_apply=True, fallback str.replace is skipped."""
    f = tmp_path / "test.py"
    f.write_text("old code\nmore stuff\n")

    fix = AIFixSuggestion(
        file=str(f),
        line=99,  # Way off -- no match near this line
        original_code="old code",
        suggested_code="new code",
    )

    result = _apply_fix(fix, auto_apply=True)
    assert_that(result).is_false()
    # File should be unchanged because fallback was skipped
    assert_that(f.read_text()).contains("old code")


def test_apply_fix_search_radius_limits_search(tmp_path):
    """A narrow search_radius can miss a match outside the radius."""
    f = tmp_path / "test.py"
    # Place target code far from line hint
    lines = ["filler\n"] * 20 + ["target code\n"]
    f.write_text("".join(lines))

    fix = AIFixSuggestion(
        file=str(f),
        line=1,  # Hint at line 1, target is at line 21
        original_code="target code",
        suggested_code="replaced code",
    )

    # With radius=2, line-targeted search won't reach line 21
    result = _apply_fix(fix, auto_apply=True, search_radius=2)
    assert_that(result).is_false()
    assert_that(f.read_text()).contains("target code")


# -- apply_fixes ---------------------------------------------------------------


def test_apply_fixes_returns_only_successful(tmp_path):
    """Verify that apply_fixes returns only successfully applied suggestions."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")

    applied = apply_fixes(
        [
            AIFixSuggestion(
                file=str(f),
                original_code="x = 1",
                suggested_code="x = 2",
            ),
            AIFixSuggestion(
                file=str(f),
                original_code="missing",
                suggested_code="x = 3",
            ),
        ],
    )
    assert_that(applied).is_length(1)
    assert_that(applied[0].suggested_code).is_equal_to("x = 2")


def test_apply_fixes_passes_auto_apply(tmp_path):
    """apply_fixes forwards auto_apply to _apply_fix."""
    f = tmp_path / "test.py"
    f.write_text("old code\nmore stuff\n")

    applied = apply_fixes(
        [
            AIFixSuggestion(
                file=str(f),
                line=99,
                original_code="old code",
                suggested_code="new code",
            ),
        ],
        auto_apply=True,
    )
    # auto_apply=True prevents fallback, so nothing should be applied
    assert_that(applied).is_empty()
    assert_that(f.read_text()).contains("old code")


# -- _group_by_code ------------------------------------------------------------


def test_group_by_code_groups_by_code():
    """Verify that fixes are grouped into separate lists by their rule code."""
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


def test_group_by_code_empty_code_uses_unknown():
    """Verify that an empty code string is grouped under the 'unknown' key."""
    fixes = [AIFixSuggestion(file="a.py", code="")]
    groups = _group_by_code(fixes)
    assert_that(groups).contains_key("unknown")


def test_group_by_code_empty_list():
    """Verify that an empty fix list produces an empty grouping."""
    groups = _group_by_code([])
    assert_that(groups).is_empty()


# -- review_fixes_interactive --------------------------------------------------


def test_review_fixes_interactive_empty_suggestions():
    """Verify that empty suggestions result in zero accepted, rejected, and applied."""
    accepted, rejected, applied = review_fixes_interactive([])
    assert_that(accepted).is_equal_to(0)
    assert_that(rejected).is_equal_to(0)
    assert_that(applied).is_empty()


def test_review_fixes_interactive_non_interactive_skips():
    """Verify that non-interactive stdin causes the review to be skipped."""
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


def test_review_fixes_interactive_prompt_text_clarifies_scope():
    """Verify that the rendered prompt includes scope clarification text."""
    prompt = _render_prompt(validate_mode=False, safe_default=False)
    assert_that(prompt).contains("accept group + remaining")
    assert_that(prompt).contains("verify fixes")


@patch("lintro.ai.interactive.sys.stdin")
@patch("lintro.ai.interactive.click.getchar")
def test_review_fixes_interactive_accept_via_keyboard(
    mock_getchar,
    mock_stdin,
    tmp_path,
):
    """Pressing 'y' accepts a group and applies fixes."""
    mock_stdin.isatty.return_value = True
    mock_getchar.return_value = "y"

    f = tmp_path / "test.py"
    f.write_text("old_code\n")

    fixes = [
        AIFixSuggestion(
            file=str(f),
            code="E501",
            original_code="old_code",
            suggested_code="new_code",
        ),
    ]

    accepted, rejected, applied = review_fixes_interactive(
        fixes,
        workspace_root=tmp_path,
    )

    assert_that(accepted).is_equal_to(1)
    assert_that(rejected).is_equal_to(0)
    assert_that(applied).is_length(1)


@patch("lintro.ai.interactive.sys.stdin")
@patch("lintro.ai.interactive.click.getchar")
def test_review_fixes_interactive_reject_via_keyboard(
    mock_getchar,
    mock_stdin,
    tmp_path,
):
    """Pressing 'r' rejects a group."""
    mock_stdin.isatty.return_value = True
    mock_getchar.return_value = "r"

    fixes = [
        AIFixSuggestion(
            file=str(tmp_path / "test.py"),
            code="B101",
            original_code="x",
            suggested_code="y",
        ),
    ]

    accepted, rejected, applied = review_fixes_interactive(fixes)

    assert_that(accepted).is_equal_to(0)
    assert_that(rejected).is_equal_to(1)
    assert_that(applied).is_empty()


@patch("lintro.ai.interactive.sys.stdin")
@patch("lintro.ai.interactive.click.getchar")
def test_review_fixes_interactive_quit_via_keyboard(
    mock_getchar,
    mock_stdin,
    tmp_path,
):
    """Pressing 'q' quits the review early."""
    mock_stdin.isatty.return_value = True
    mock_getchar.return_value = "q"

    fixes = [
        AIFixSuggestion(
            file=str(tmp_path / "a.py"),
            code="B101",
            original_code="x",
            suggested_code="y",
        ),
        AIFixSuggestion(
            file=str(tmp_path / "b.py"),
            code="E501",
            original_code="a",
            suggested_code="b",
        ),
    ]

    accepted, rejected, applied = review_fixes_interactive(fixes)

    assert_that(accepted).is_equal_to(0)
    # Only the first group was seen before quit
    assert_that(applied).is_empty()
