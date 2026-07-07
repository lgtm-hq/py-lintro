"""Unit tests for RubocopPlugin check and fix execution (mocked subprocess)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.rubocop import RubocopPlugin

from .conftest import make_ctx, make_result, offense, rubocop_json


def test_check_no_issues(rubocop_plugin: RubocopPlugin, tmp_path: Path) -> None:
    """Check succeeds and reports zero issues on clean output.

    Args:
        rubocop_plugin: The plugin under test.
        tmp_path: Temporary directory fixture.
    """
    clean = rubocop_json([])
    with (
        patch.object(rubocop_plugin, "_prepare_execution") as mock_prep,
        patch.object(
            rubocop_plugin,
            "_run_subprocess_result",
            return_value=make_result(clean, returncode=0),
        ),
    ):
        mock_prep.return_value = make_ctx(tmp_path, ["app.rb"])
        result = rubocop_plugin.check(["app.rb"], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_issues(rubocop_plugin: RubocopPlugin, tmp_path: Path) -> None:
    """Check surfaces parsed offenses and fails.

    Args:
        rubocop_plugin: The plugin under test.
        tmp_path: Temporary directory fixture.
    """
    payload = rubocop_json(
        [
            offense(cop_name="Layout/SpaceInsideParens"),
            offense(cop_name="Naming/MethodParameterName", correctable=False),
        ],
    )
    with (
        patch.object(rubocop_plugin, "_prepare_execution") as mock_prep,
        patch.object(
            rubocop_plugin,
            "_run_subprocess_result",
            return_value=make_result(payload),
        ),
    ):
        mock_prep.return_value = make_ctx(tmp_path, ["app.rb"])
        result = rubocop_plugin.check(["app.rb"], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    codes = [i.code for i in result.issues]  # type: ignore[union-attr]
    assert_that(codes).contains("Layout/SpaceInsideParens")


def test_check_ignores_stderr_noise(
    rubocop_plugin: RubocopPlugin,
    tmp_path: Path,
) -> None:
    """Only stdout is parsed; stderr diagnostics do not corrupt results.

    Args:
        rubocop_plugin: The plugin under test.
        tmp_path: Temporary directory fixture.
    """
    payload = rubocop_json([offense()])
    noisy = make_result(payload)
    # Simulate RuboCop emitting a "new cops" notice on stderr.
    noisy = noisy.__class__(
        returncode=1,
        stdout=payload,
        stderr="The following cops were added to RuboCop...",
        output=payload + "The following cops were added to RuboCop...",
    )
    with (
        patch.object(rubocop_plugin, "_prepare_execution") as mock_prep,
        patch.object(
            rubocop_plugin,
            "_run_subprocess_result",
            return_value=noisy,
        ),
    ):
        mock_prep.return_value = make_ctx(tmp_path, ["app.rb"])
        result = rubocop_plugin.check(["app.rb"], {})

    assert_that(result.issues_count).is_equal_to(1)


def test_fix_invariant(rubocop_plugin: RubocopPlugin, tmp_path: Path) -> None:
    """Fix maintains initial == fixed + remaining.

    Args:
        rubocop_plugin: The plugin under test.
        tmp_path: Temporary directory fixture.
    """
    initial = rubocop_json(
        [
            offense(cop_name="Style/StringLiterals", correctable=True),
            offense(cop_name="Layout/SpaceInsideParens", correctable=True),
            offense(cop_name="Naming/MethodParameterName", correctable=False),
        ],
    )
    remaining = rubocop_json(
        [offense(cop_name="Naming/MethodParameterName", correctable=False)],
    )
    # check -> initial, fix run -> ignored, check -> remaining
    side_effects = [
        make_result(initial),
        make_result(initial),
        make_result(remaining),
    ]
    with (
        patch.object(rubocop_plugin, "_prepare_execution") as mock_prep,
        patch.object(
            rubocop_plugin,
            "_run_subprocess_result",
            side_effect=side_effects,
        ),
    ):
        mock_prep.return_value = make_ctx(tmp_path, ["app.rb"])
        result = rubocop_plugin.fix(["app.rb"], {})

    assert_that(result.initial_issues_count).is_equal_to(3)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.initial_issues_count).is_equal_to(
        result.fixed_issues_count + result.remaining_issues_count,
    )
    assert_that(result.success).is_false()


def test_check_skips_when_no_files(
    rubocop_plugin: RubocopPlugin,
    tmp_path: Path,
) -> None:
    """Check returns the early result when preparation signals a skip.

    Args:
        rubocop_plugin: The plugin under test.
        tmp_path: Temporary directory fixture.
    """
    from unittest.mock import MagicMock

    early = MagicMock()
    ctx = MagicMock()
    ctx.should_skip = True
    ctx.early_result = early
    with patch.object(rubocop_plugin, "_prepare_execution", return_value=ctx):
        result = rubocop_plugin.check(["app.rb"], {})
    assert_that(result).is_same_as(early)
