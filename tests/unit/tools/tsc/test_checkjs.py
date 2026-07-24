"""Unit tests for tsc checkJs / JSDoc JavaScript activation (issue #1185)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.tsc import TscPlugin
from tests.unit.utils.tsconfig_helpers import write_tsconfig

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def js_checkjs_project(tmp_path: Path) -> Path:
    """JS project with checkJs enabled and a deliberate type error.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the project root.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {
                "strict": True,
                "checkJs": True,
                "noEmit": True,
                "allowJs": True,
            },
            "include": ["*.js"],
        },
    )
    (tmp_path / "bad.js").write_text(
        "/** @type {number} */\nconst x = 'not-a-number';\nexport { x };\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def js_no_checkjs_project(tmp_path: Path) -> Path:
    """JS project with a tsconfig that does not enable checkJs.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the project root.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {
                "strict": True,
                "noEmit": True,
                "allowJs": True,
            },
            "include": ["*.js"],
        },
    )
    (tmp_path / "plain.js").write_text(
        "/** @type {number} */\nconst x = 'not-a-number';\nexport { x };\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def mixed_ts_js_checkjs_project(tmp_path: Path) -> Path:
    """Mixed TypeScript + JavaScript project with checkJs enabled.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the project root.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {
                "strict": True,
                "checkJs": True,
                "noEmit": True,
                "allowJs": True,
            },
            "include": ["*.ts", "*.js"],
        },
    )
    (tmp_path / "ok.ts").write_text(
        "export const n: number = 1;\n",
        encoding="utf-8",
    )
    (tmp_path / "bad.js").write_text(
        "/** @type {number} */\nconst x = 'not-a-number';\nexport { x };\n",
        encoding="utf-8",
    )
    return tmp_path


# =============================================================================
# Activation / early-skip behavior
# =============================================================================


def test_js_with_checkjs_activates_and_reports_issues(
    tsc_plugin: TscPlugin,
    js_checkjs_project: Path,
) -> None:
    """JS + checkJs runs tsc and surfaces JSDoc type errors.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        js_checkjs_project: Fixture project with checkJs and a bad .js file.
    """
    js_file = js_checkjs_project / "bad.js"
    tsc_output = (
        f"{js_file}(2,7): error TS2322: Type 'string' is not assignable "
        "to type 'number'."
    )

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(False, tsc_output),
        ) as mock_run:
            result = tsc_plugin.check([str(js_checkjs_project)], {})

    assert_that(result.skipped).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(mock_run.called).is_true()


def test_js_without_checkjs_skips_early(
    tsc_plugin: TscPlugin,
    js_no_checkjs_project: Path,
) -> None:
    """JS-only without checkJs skips before invoking tsc.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        js_no_checkjs_project: Fixture project without checkJs.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ) as mock_run:
            result = tsc_plugin.check([str(js_no_checkjs_project)], {})

    assert_that(result.skipped).is_true()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.skip_reason).contains("checkJs")
    assert_that(mock_run.called).is_false()


def test_js_only_without_tsconfig_skips_early(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Bare JS files with no tsconfig do not activate tsc.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    js_file = tmp_path / "alone.js"
    js_file.write_text("export const x = 1;\n", encoding="utf-8")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ) as mock_run:
            result = tsc_plugin.check([str(js_file)], {})

    assert_that(result.skipped).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(mock_run.called).is_false()


def test_mixed_ts_js_with_checkjs_runs(
    tsc_plugin: TscPlugin,
    mixed_ts_js_checkjs_project: Path,
) -> None:
    """Mixed TS+JS with checkJs still invokes tsc (does not early-skip).

    Args:
        tsc_plugin: The TscPlugin instance to test.
        mixed_ts_js_checkjs_project: Fixture with .ts and .js plus checkJs.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ) as mock_run:
            result = tsc_plugin.check([str(mixed_ts_js_checkjs_project)], {})

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()


def test_checkjs_inherited_via_extends_activates(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Inherited checkJs through extends still activates JS-only checks.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"compilerOptions": {"checkJs": True, "allowJs": True, "strict": True}},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./tsconfig.base.json", "include": ["*.js"]},
    )
    (tmp_path / "app.js").write_text(
        "/** @type {number} */\nconst x = 1;\nexport { x };\n",
        encoding="utf-8",
    )

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ) as mock_run:
            result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()


def test_explicit_project_without_checkjs_skips_js_only(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Explicit project option without checkJs still skips JS-only runs.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True}, "include": ["*.js"]},
    )
    js_file = tmp_path / "app.js"
    js_file.write_text("export const x = 1;\n", encoding="utf-8")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ) as mock_run:
            result = tsc_plugin.check(
                [str(js_file)],
                {"project": str(tmp_path / "tsconfig.json")},
            )

    assert_that(result.skipped).is_true()
    assert_that(mock_run.called).is_false()


def test_mjs_and_cjs_patterns_are_discovered(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """*.mjs and *.cjs files are discovered by the tsc plugin patterns.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {"checkJs": True, "allowJs": True},
            "include": ["*.mjs", "*.cjs"],
        },
    )
    (tmp_path / "mod.mjs").write_text("export const a = 1;\n", encoding="utf-8")
    (tmp_path / "mod.cjs").write_text("module.exports = { a: 1 };\n", encoding="utf-8")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        ctx = tsc_plugin._prepare_execution(
            [str(tmp_path)],
            dict(tsc_plugin.options),
            no_files_message="none",
        )

    assert_that(ctx.should_skip).is_false()
    suffixes = {Path(f).suffix for f in ctx.files}
    assert_that(suffixes).contains(".mjs", ".cjs")


def test_is_js_only_helpers() -> None:
    """_is_js_only distinguishes pure-JS from mixed/TS inputs."""
    assert_that(TscPlugin._is_js_only(["/a/app.js", "/a/lib.mjs"])).is_true()
    assert_that(TscPlugin._is_js_only(["/a/app.ts"])).is_false()
    assert_that(TscPlugin._is_js_only(["/a/app.js", "/a/app.ts"])).is_false()
    assert_that(TscPlugin._is_js_only([])).is_false()
    assert_that(TscPlugin._is_js_only(["/a/readme.md"])).is_false()
