"""Unit tests for tsc checkJs / JSDoc JavaScript activation (issue #1185)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.tsc import TscPlugin
from tests.unit.utils.tsconfig_helpers import write_tsconfig

# =============================================================================
# Helpers
# =============================================================================


def _run_check(
    plugin: TscPlugin,
    paths: list[str],
    options: dict[str, object] | None = None,
    *,
    subprocess_result: tuple[bool, str] = (True, ""),
) -> tuple[Any, MagicMock]:
    """Invoke ``plugin.check`` with version verification and tsc mocked.

    Args:
        plugin: Plugin under test.
        paths: Paths passed to ``check``.
        options: Runtime options passed to ``check``.
        subprocess_result: Return value for ``_run_subprocess``.

    Returns:
        A ``(ToolResult, mock_run)`` pair.
    """
    mock_run = MagicMock(return_value=subprocess_result)
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            plugin,
            "_run_subprocess",
            mock_run,
        ):
            result = plugin.check(paths, options or {})
    return result, mock_run


def _write_plain_js(path: Path) -> Path:
    """Write a small JS module at *path*.

    Args:
        path: Destination file.

    Returns:
        The written path.
    """
    path.write_text("export const x = 1;\n", encoding="utf-8")
    return path


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
    result, mock_run = _run_check(
        tsc_plugin,
        [str(js_checkjs_project)],
        subprocess_result=(False, tsc_output),
    )

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
    result, mock_run = _run_check(
        tsc_plugin,
        [str(js_no_checkjs_project)],
    )

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
    js_file = _write_plain_js(tmp_path / "alone.js")
    result, mock_run = _run_check(tsc_plugin, [str(js_file)])

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
    result, mock_run = _run_check(
        tsc_plugin,
        [str(mixed_ts_js_checkjs_project)],
    )

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
    _write_plain_js(tmp_path / "app.js")

    result, mock_run = _run_check(tsc_plugin, [str(tmp_path)])

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()


def test_mixed_without_allowjs_omits_js_from_temp_config(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Mixed TS+JS without allowJs/checkJs keeps .ts and drops .js (no TS6504).

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True, "noEmit": True}},
    )
    (tmp_path / "ok.ts").write_text(
        "export const n: number = 1;\n",
        encoding="utf-8",
    )
    _write_plain_js(tmp_path / "plain.js")

    captured_files: list[str] = []
    original_create = tsc_plugin._create_temp_tsconfig

    def _spy_create(*args: Any, **kwargs: Any) -> Path:
        files = kwargs.get("files")
        if files is None and len(args) >= 2:
            files = args[1]
        captured_files.extend(list(files or []))
        return original_create(*args, **kwargs)

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_create_temp_tsconfig",
            side_effect=_spy_create,
        ):
            with patch.object(
                tsc_plugin,
                "_run_subprocess",
                return_value=(True, ""),
            ) as mock_run:
                result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()
    suffixes = {Path(name).suffix.lower() for name in captured_files}
    assert_that(suffixes).contains(".ts")
    assert_that(".js" in suffixes).is_false()


def test_ts_check_pragma_does_not_skip_js_only(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """JS-only trees with ``// @ts-check`` still invoke tsc without checkJs.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True, "noEmit": True}},
    )
    (tmp_path / "checked.js").write_text(
        "// @ts-check\n/** @type {number} */\nconst x = 'nope';\nexport { x };\n",
        encoding="utf-8",
    )
    js_file = tmp_path / "checked.js"
    tsc_output = (
        f"{js_file}(3,7): error TS2322: Type 'string' is not assignable "
        "to type 'number'."
    )
    result, mock_run = _run_check(
        tsc_plugin,
        [str(tmp_path)],
        subprocess_result=(False, tsc_output),
    )

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()
    assert_that(result.issues_count).is_greater_than(0)


def test_ts_check_temp_config_enables_allowjs(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Temp tsconfig sets allowJs so ``@ts-check`` JS files are loaded.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True, "noEmit": True}},
    )
    (tmp_path / "checked.js").write_text(
        "// @ts-check\nexport const x = 1;\n",
        encoding="utf-8",
    )
    captured: list[dict[str, object]] = []
    original_create = tsc_plugin._create_temp_tsconfig

    def _spy_create(*args: Any, **kwargs: Any) -> Path:
        path = original_create(*args, **kwargs)
        captured.append(json.loads(path.read_text(encoding="utf-8")))
        return path

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_create_temp_tsconfig",
            side_effect=_spy_create,
        ):
            with patch.object(
                tsc_plugin,
                "_run_subprocess",
                return_value=(True, ""),
            ):
                result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.skipped).is_false()
    assert_that(captured).is_not_empty()
    compiler_options = captured[0]["compilerOptions"]
    assert isinstance(compiler_options, dict)
    assert_that(compiler_options.get("allowJs")).is_true()


def test_ts_nocheck_pragma_still_skips_js_only(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """``@ts-nocheck`` does not keep a JS-only tree alive without checkJs.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True, "noEmit": True}},
    )
    (tmp_path / "plain.js").write_text(
        "// @ts-nocheck\nexport const x = 1;\n",
        encoding="utf-8",
    )
    result, mock_run = _run_check(tsc_plugin, [str(tmp_path)])

    assert_that(result.skipped).is_true()
    assert_that(mock_run.called).is_false()


def test_unresolved_relative_extends_does_not_skip(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Missing extends targets fail closed: JS-only checks are not skipped.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "extends": "./missing-base.json",
            "include": ["*.js"],
        },
    )
    _write_plain_js(tmp_path / "app.js")

    result, mock_run = _run_check(tsc_plugin, [str(tmp_path)])

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()


def test_unresolved_package_extends_reaches_install_gate(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Unresolved npm extends does not skip for checkJs before install.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "extends": "@tsconfig/strictest/tsconfig.json",
            "include": ["*.js"],
        },
    )
    (tmp_path / "package.json").write_text(
        '{"name": "demo", "version": "1.0.0"}\n',
        encoding="utf-8",
    )
    _write_plain_js(tmp_path / "app.js")

    result, mock_run = _run_check(tsc_plugin, [str(tmp_path)])

    assert_that(result.skipped).is_true()
    assert_that(result.skip_reason).is_equal_to("node_modules not found")
    assert_that(mock_run.called).is_false()


def test_js_only_use_project_files_still_invokes_tsc(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Passing only a JS path with use_project_files still runs native tsc.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {"strict": True, "noEmit": True},
            "include": ["*.ts"],
        },
    )
    error_file = tmp_path / "error.ts"
    error_file.write_text(
        "const y: number = 'string';\nexport { y };\n",
        encoding="utf-8",
    )
    js_file = _write_plain_js(tmp_path / "plain.js")
    tsc_output = (
        f"{error_file}(1,7): error TS2322: Type 'string' is not assignable "
        "to type 'number'."
    )
    result, mock_run = _run_check(
        tsc_plugin,
        [str(js_file)],
        {"use_project_files": True},
        subprocess_result=(False, tsc_output),
    )

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()
    assert_that(result.issues_count).is_greater_than(0)


def test_js_only_explicit_project_still_invokes_tsc(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Explicit project option does not skip just because the input is JS.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
    """
    tsconfig = write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {"strict": True, "noEmit": True},
            "include": ["*.ts"],
        },
    )
    error_file = tmp_path / "error.ts"
    error_file.write_text(
        "const y: number = 'string';\nexport { y };\n",
        encoding="utf-8",
    )
    js_file = _write_plain_js(tmp_path / "plain.js")
    tsc_output = (
        f"{error_file}(1,7): error TS2322: Type 'string' is not assignable "
        "to type 'number'."
    )
    result, mock_run = _run_check(
        tsc_plugin,
        [str(js_file)],
        {"project": str(tsconfig)},
        subprocess_result=(False, tsc_output),
    )

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()
    assert_that(result.issues_count).is_greater_than(0)


@pytest.mark.parametrize(
    "suffix",
    [".js", ".mjs", ".cjs", ".jsx"],
    ids=["js", "mjs", "cjs", "jsx"],
)
def test_js_suffix_with_checkjs_invokes_tsc(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
    suffix: str,
) -> None:
    """Each JS suffix is discovered and checked when checkJs is enabled.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
        suffix: JavaScript file suffix under test.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {"checkJs": True, "allowJs": True, "noEmit": True},
            "include": [f"*{suffix}"],
        },
    )
    (tmp_path / f"mod{suffix}").write_text(
        "export const a = 1;\n",
        encoding="utf-8",
    )
    result, mock_run = _run_check(tsc_plugin, [str(tmp_path)])

    assert_that(result.skipped).is_false()
    assert_that(mock_run.called).is_true()


@pytest.mark.parametrize(
    "suffix",
    [".js", ".mjs", ".cjs", ".jsx"],
    ids=["js", "mjs", "cjs", "jsx"],
)
def test_js_suffix_without_checkjs_skips(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
    suffix: str,
) -> None:
    """Each JS suffix is treated as JS-only skip input without checkJs.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Pytest temporary directory.
        suffix: JavaScript file suffix under test.
    """
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True, "noEmit": True}},
    )
    (tmp_path / f"mod{suffix}").write_text(
        "export const a = 1;\n",
        encoding="utf-8",
    )
    result, mock_run = _run_check(tsc_plugin, [str(tmp_path)])

    assert_that(result.skipped).is_true()
    assert_that(mock_run.called).is_false()
