"""Unit tests for tsc plugin execution."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.tsc import TscPlugin

# =============================================================================
# Tests for TscPlugin.check method
# =============================================================================


def test_check_with_mocked_subprocess_success(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 42;\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ):
            result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when tsc finds type errors.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 'string';\n")

    tsc_output = f"{test_file}(1,7): error TS2322: Type 'string' is not assignable to type 'number'."

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(False, tsc_output),
        ):
            result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_timeout(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 42;\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["tsc"], timeout=60),
        ):
            result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_no_typescript_files(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no TypeScript files found.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_ts_file = tmp_path / "test.txt"
    non_ts_file.write_text("Not a TypeScript file")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = tsc_plugin.check([str(non_ts_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No .ts/.tsx/.mts/.cts files")


def test_check_parses_multiple_issues(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check correctly parses multiple issues from output.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 'a';\nconst y: string = 42;\n")

    tsc_output = f"""{test_file}(1,7): error TS2322: Type 'string' is not assignable to type 'number'.
{test_file}(2,7): error TS2322: Type 'number' is not assignable to type 'string'."""

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(False, tsc_output),
        ):
            result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)


# =============================================================================
# Tests for issue #851 — respect tsconfig include/exclude
# =============================================================================


def test_check_respects_tsconfig_include(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """When tsconfig has explicit include, no temp config is created.

    The plugin should run tsc -p <tsconfig> directly, respecting the
    project's scoping rather than overriding it with all discovered files.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create tsconfig with explicit include at the project root
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(
        json.dumps({"include": ["*.ts"], "compilerOptions": {"strict": True}}),
    )

    # Create a .ts file alongside the tsconfig
    test_file = tmp_path / "app.ts"
    test_file.write_text("const x: number = 42;\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ) as mock_run:
            result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()

    # Verify --project points to the real tsconfig (not a temp file)
    cmd = mock_run.call_args[1].get("cmd") or mock_run.call_args[0][0]
    project_idx = cmd.index("--project")
    project_arg = cmd[project_idx + 1]
    assert_that(project_arg).is_equal_to(str(tsconfig.resolve()))


def test_check_creates_temp_when_no_include(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """When tsconfig has no include, temp config is created (backward compat).

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create tsconfig WITHOUT include
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(
        json.dumps({"compilerOptions": {"strict": True}}),
    )

    test_file = tmp_path / "app.ts"
    test_file.write_text("const x: number = 42;\n")

    temp_created = False
    original_create = tsc_plugin._create_temp_tsconfig

    def spy_create(*args: Any, **kwargs: Any) -> Path:
        nonlocal temp_created
        temp_created = True
        return original_create(*args, **kwargs)

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_create_temp_tsconfig",
            side_effect=spy_create,
        ):
            with patch.object(
                tsc_plugin,
                "_run_subprocess",
                return_value=(True, ""),
            ):
                tsc_plugin.check([str(test_file)], {})

    assert_that(temp_created).is_true()


# =============================================================================
# Tests for multi-project support
# =============================================================================


def test_check_multi_project_runs_per_project(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """When multiple tsconfigs found, tsc runs once per project.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create monorepo with two sub-projects
    api_dir = tmp_path / "packages" / "api"
    web_dir = tmp_path / "packages" / "web"
    api_dir.mkdir(parents=True)
    web_dir.mkdir(parents=True)

    (api_dir / "tsconfig.json").write_text(
        json.dumps({"include": ["src/**/*.ts"]}),
    )
    (web_dir / "tsconfig.json").write_text(
        json.dumps({"include": ["src/**/*.ts"]}),
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps(
            {
                "references": [
                    {"path": "./packages/api"},
                    {"path": "./packages/web"},
                ],
            },
        ),
    )

    # Create test files
    (api_dir / "src").mkdir()
    (web_dir / "src").mkdir()
    api_file = api_dir / "src" / "server.ts"
    web_file = web_dir / "src" / "app.ts"
    api_file.write_text("const x: number = 42;\n")
    web_file.write_text("const y: string = 'hello';\n")

    run_count = 0
    observed_cwds: list[str] = []

    def mock_run(
        cmd: list[str],
        timeout: float = 60,
        cwd: str | None = None,
        **kwargs: object,
    ) -> tuple[bool, str]:
        nonlocal run_count
        run_count += 1
        if cwd:
            observed_cwds.append(cwd)
        return (True, "")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(tsc_plugin, "_run_subprocess", side_effect=mock_run):
            result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    # Should run at least twice (once per sub-project)
    assert_that(run_count).is_greater_than_or_equal_to(2)
    # Each sub-project directory should have been used as the working directory
    api_cwd = str(api_dir.resolve())
    web_cwd = str(web_dir.resolve())
    assert_that(observed_cwds).contains(api_cwd)
    assert_that(observed_cwds).contains(web_cwd)


def test_check_multi_project_aggregates_issues(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Multi-project mode aggregates issues across sub-projects.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    api_dir = tmp_path / "packages" / "api"
    web_dir = tmp_path / "packages" / "web"
    api_dir.mkdir(parents=True)
    web_dir.mkdir(parents=True)

    (api_dir / "tsconfig.json").write_text(
        json.dumps({"include": ["src/**/*.ts"]}),
    )
    (web_dir / "tsconfig.json").write_text(
        json.dumps({"include": ["src/**/*.ts"]}),
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps(
            {
                "references": [
                    {"path": "./packages/api"},
                    {"path": "./packages/web"},
                ],
            },
        ),
    )

    (api_dir / "src").mkdir()
    (web_dir / "src").mkdir()
    api_file = api_dir / "src" / "server.ts"
    web_file = web_dir / "src" / "app.ts"
    api_file.write_text("const x: number = 42;\n")
    web_file.write_text("const y: string = 'hello';\n")

    def mock_run(
        cmd: list[str],
        timeout: float = 60,
        cwd: str | None = None,
        **kwargs: object,
    ) -> tuple[bool, str]:
        # Return one error per project
        if cwd and "api" in cwd:
            return (False, f"{api_file}(1,7): error TS2322: Type mismatch.")
        return (False, f"{web_file}(1,7): error TS2322: Type mismatch.")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(tsc_plugin, "_run_subprocess", side_effect=mock_run):
            result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than_or_equal_to(2)


def test_check_single_project_backward_compat(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Single tsconfig behaves identically to previous behavior.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 42;\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ):
            result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


# =============================================================================
# Tests for TscPlugin.fix method
# =============================================================================


def test_fix_raises_not_implemented(tsc_plugin: TscPlugin) -> None:
    """Fix raises NotImplementedError.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        tsc_plugin.fix(paths=["src"], options={})
