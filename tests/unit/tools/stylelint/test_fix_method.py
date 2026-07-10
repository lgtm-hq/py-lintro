"""Tests for StylelintPlugin.fix method."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.stylelint import StylelintPlugin
from tests.unit.tools.stylelint.conftest import make_ctx

INITIAL_JSON = (
    '[{"source":"/tmp/a.css","warnings":['
    '{"line":2,"column":10,"rule":"color-hex-length","severity":"error",'
    '"text":"Expected \\"#FFFFFF\\" to be \\"#FFF\\" (color-hex-length)"},'
    '{"line":5,"column":8,"rule":"block-no-empty","severity":"error",'
    '"text":"Empty block (block-no-empty)"}]}]'
)
# After --fix: color-hex-length auto-fixed, empty block remains.
REMAINING_JSON = (
    '[{"source":"/tmp/a.css","warnings":['
    '{"line":4,"column":8,"rule":"block-no-empty","severity":"error",'
    '"text":"Empty block (block-no-empty)"}]}]'
)
CLEAN_JSON = '[{"source":"/tmp/a.css","errored":false,"warnings":[]}]'
NO_CONFIG = "ConfigurationError: No configuration provided for /tmp/a.css"


def test_fix_partial_preserves_invariant(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Fix reports fixed/remaining counts satisfying the count invariant."""
    (tmp_path / "a.css").write_text("a { color: #FFFFFF; }\n.x {}\n")

    # check -> fix -> re-check
    outputs = [
        (False, INITIAL_JSON),
        (False, INITIAL_JSON),
        (False, REMAINING_JSON),
    ]

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(stylelint_plugin, "_run_subprocess", side_effect=outputs),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        result = stylelint_plugin.fix([str(tmp_path / "a.css")], {})

    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    fixed = result.fixed_issues_count or 0
    remaining = result.remaining_issues_count or 0
    assert_that(fixed + remaining).is_equal_to(result.initial_issues_count)
    assert_that(result.success).is_false()
    assert_that(result.output).contains("Fixed 1")


def test_fix_all_resolved(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Fix succeeds when every issue is auto-fixed."""
    (tmp_path / "a.css").write_text("a { color: #FFFFFF; }\n")

    outputs = [
        (False, INITIAL_JSON),
        (True, CLEAN_JSON),
        (True, CLEAN_JSON),
    ]

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(stylelint_plugin, "_run_subprocess", side_effect=outputs),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        result = stylelint_plugin.fix([str(tmp_path / "a.css")], {})

    assert_that(result.success).is_true()
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.output).contains("successfully auto-fixed")


def test_fix_uses_fix_flag(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """The fix path invokes stylelint with the --fix flag."""
    (tmp_path / "a.css").write_text("a { color: #FFFFFF; }\n")

    outputs = [
        (True, CLEAN_JSON),
        (True, CLEAN_JSON),
        (True, CLEAN_JSON),
    ]
    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            side_effect=outputs,
        ) as run,
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        stylelint_plugin.fix([str(tmp_path / "a.css")], {})

    fix_cmd = run.call_args_list[1].kwargs["cmd"]
    assert_that(fix_cmd).contains("--fix")


def test_fix_skips_when_no_config(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns a non-error skip when no stylelint config exists."""
    (tmp_path / "a.css").write_text("a { color: red; }\n")

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            return_value=(False, NO_CONFIG),
        ),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        result = stylelint_plugin.fix([str(tmp_path / "a.css")], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("no stylelint configuration")
