"""Characterization tests for the shared TypeScript-checker base.

These tests lock in the observable behavior shared by ``tsc`` and ``vue-tsc``
after their common implementation was extracted into
:class:`lintro.tools.definitions._ts_checker_base.TypeScriptCheckerPlugin`.

They deliberately assert on user-facing output copy, command construction,
tsconfig discovery priority, framework deferral, and dependency-error shaping
so that the deduplication cannot silently change behavior. The per-tool deltas
(binary command, file extensions, parser wiring, error copy, and tsc's
framework detection) are covered explicitly alongside the shared shape.
"""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.definitions._ts_checker_base import TypeScriptCheckerPlugin
from lintro.tools.definitions.tsc import TscPlugin
from lintro.tools.definitions.vue_tsc import VueTscPlugin


def _run_check(
    *,
    plugin: TypeScriptCheckerPlugin,
    paths: list[str],
    subprocess_return: tuple[bool, str],
) -> ToolResult:
    """Run a plugin ``check`` with the subprocess and version check mocked.

    Args:
        plugin: The checker plugin under test.
        paths: Paths to pass to ``check``.
        subprocess_return: The ``(success, output)`` tuple the mocked
            subprocess should return.

    Returns:
        The ToolResult produced by ``check``.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            plugin,
            "_run_subprocess",
            return_value=subprocess_return,
        ):
            return plugin.check(paths, {})


# =============================================================================
# Shared inheritance shape
# =============================================================================


def test_both_plugins_share_base() -> None:
    """Both concrete plugins derive from the shared TypeScript-checker base."""
    assert_that(issubclass(TscPlugin, TypeScriptCheckerPlugin)).is_true()
    assert_that(issubclass(VueTscPlugin, TypeScriptCheckerPlugin)).is_true()


@pytest.mark.parametrize(
    ("plugin_cls", "expected_label", "expected_prefix"),
    [
        (TscPlugin, "tsc", ".lintro-tsc-"),
        (VueTscPlugin, "vue-tsc", ".lintro-vue-tsc-"),
    ],
)
def test_per_tool_class_config(
    *,
    plugin_cls: type[TypeScriptCheckerPlugin],
    expected_label: str,
    expected_prefix: str,
) -> None:
    """Each concrete plugin declares its own label and temp-config prefix.

    Args:
        plugin_cls: The concrete plugin class under test.
        expected_label: Expected ``_tool_label`` value.
        expected_prefix: Expected ``_temp_config_prefix`` value.
    """
    assert_that(plugin_cls._tool_label).is_equal_to(expected_label)
    assert_that(plugin_cls._temp_config_prefix).is_equal_to(expected_prefix)


# =============================================================================
# Command construction equivalence
# =============================================================================


def test_build_command_shared_flags_equivalent() -> None:
    """Both tools build the same core flag sequence around their binary."""
    tsc_cmd = TscPlugin()._build_command(files=["src/a.ts"])
    vue_cmd = VueTscPlugin()._build_command(files=["src/a.vue"])

    for cmd in (tsc_cmd, vue_cmd):
        assert_that(cmd).contains("--noEmit")
        assert_that(cmd).contains("--pretty")
        assert_that(cmd).contains("false")
        assert_that(cmd).contains("--skipLibCheck")


def test_build_command_omits_files_when_project_supplied() -> None:
    """When a project path is supplied, raw file args are not appended."""
    cmd = TscPlugin()._build_command(
        files=["src/a.ts"],
        project_path="/tmp/tsconfig.json",
    )

    assert_that(cmd).contains("--project")
    assert_that(cmd).does_not_contain("src/a.ts")


# =============================================================================
# tsconfig discovery priority (per-tool delta)
# =============================================================================


def test_find_tsconfig_tsc_uses_plain_tsconfig(tmp_path: Path) -> None:
    """Tsc discovers ``tsconfig.json`` in the working directory.

    Args:
        tmp_path: Temporary directory for the fixture tsconfig.
    """
    (tmp_path / "tsconfig.json").write_text("{}")

    result = TscPlugin()._find_tsconfig(tmp_path)

    assert_that(result).is_equal_to(tmp_path / "tsconfig.json")


def test_find_tsconfig_vue_prefers_app_config(tmp_path: Path) -> None:
    """vue-tsc prefers ``tsconfig.app.json`` over ``tsconfig.json``.

    Args:
        tmp_path: Temporary directory for the fixture tsconfigs.
    """
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "tsconfig.app.json").write_text("{}")

    result = VueTscPlugin()._find_tsconfig(tmp_path)

    assert_that(result).is_equal_to(tmp_path / "tsconfig.app.json")


def test_find_tsconfig_tsc_ignores_app_config(tmp_path: Path) -> None:
    """Tsc only discovers ``tsconfig.json`` and ignores ``tsconfig.app.json``.

    Args:
        tmp_path: Temporary directory for the fixture tsconfigs.
    """
    (tmp_path / "tsconfig.app.json").write_text("{}")

    result = TscPlugin()._find_tsconfig(tmp_path)

    assert_that(result).is_none()


# =============================================================================
# Config preference on the check() path (issue #1112)
# =============================================================================


def _project_paths_from_check(
    *,
    plugin: TypeScriptCheckerPlugin,
    paths: list[str],
) -> list[str]:
    """Run ``check`` and return the ``--project`` path from every checker call.

    The checker subprocess is mocked to always succeed while recording the
    commands it is asked to run, so the tsconfig each invocation targets can
    be observed without executing a real compiler.

    Args:
        plugin: The checker plugin under test.
        paths: Paths to pass to ``check``.

    Returns:
        The ``--project`` argument from each recorded subprocess command,
        in call order.
    """
    calls: list[list[str]] = []

    def _record(*, cmd: list[str], timeout: int, cwd: str) -> tuple[bool, str]:
        calls.append(cmd)
        return (True, "")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(plugin, "_run_subprocess", side_effect=_record):
            plugin.check(paths, {})

    return [cmd[cmd.index("--project") + 1] for cmd in calls if "--project" in cmd]


@pytest.fixture
def both_configs_project(tmp_path: Path) -> Path:
    """A project directory containing both tsconfig.json and tsconfig.app.json.

    Mirrors a Vite Vue layout: vue-tsc should prefer ``tsconfig.app.json``
    while tsc uses ``tsconfig.json``. Both configs carry explicit ``include``
    scoping so the checker runs ``-p`` against the config directly (no temp
    tsconfig), making the selected config observable in the command.

    Args:
        tmp_path: Temporary directory for the fixture project.

    Returns:
        Path to the project root containing both configs and source files.
    """
    (tmp_path / "tsconfig.json").write_text('{"include": ["*.ts"]}')
    (tmp_path / "tsconfig.app.json").write_text('{"include": ["*.vue", "*.ts"]}')
    (tmp_path / "main.ts").write_text("export const x: number = 1;\n")
    (tmp_path / "App.vue").write_text("<template><div/></template>\n")
    return tmp_path


def test_check_vue_tsc_prefers_app_config_over_generic_discovery(
    both_configs_project: Path,
) -> None:
    """vue-tsc's ``check()`` selects tsconfig.app.json when both configs exist.

    Regression test for issue #1112: generic discovery previously bypassed
    the VueTscPlugin ``tsconfig.app.json`` preference on the check() path.

    Args:
        both_configs_project: Fixture project with both tsconfigs present.
    """
    project_paths = _project_paths_from_check(
        plugin=VueTscPlugin(),
        paths=[str(both_configs_project)],
    )

    expected = (both_configs_project / "tsconfig.app.json").resolve()
    assert_that(project_paths).is_length(1)
    assert_that(Path(project_paths[0]).resolve()).is_equal_to(expected)


def test_check_tsc_uses_plain_config_when_both_present(
    both_configs_project: Path,
) -> None:
    """Tsc's ``check()`` selects tsconfig.json even when tsconfig.app.json exists.

    tsc declares only ``tsconfig.json`` as a candidate, so the #1112 fix must
    not change its selection.

    Args:
        both_configs_project: Fixture project with both tsconfigs present.
    """
    project_paths = _project_paths_from_check(
        plugin=TscPlugin(),
        paths=[str(both_configs_project)],
    )

    expected = (both_configs_project / "tsconfig.json").resolve()
    assert_that(project_paths).is_length(1)
    assert_that(Path(project_paths[0]).resolve()).is_equal_to(expected)


def test_preferred_candidate_tsconfig_vue_selects_app_config(
    tmp_path: Path,
) -> None:
    """vue-tsc resolves tsconfig.app.json as its preferred candidate.

    Args:
        tmp_path: Temporary directory holding both configs.
    """
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "tsconfig.app.json").write_text("{}")

    result = VueTscPlugin()._preferred_candidate_tsconfig(tmp_path)

    assert_that(result).is_equal_to((tmp_path / "tsconfig.app.json").resolve())


def test_preferred_candidate_tsconfig_vue_none_without_app_config(
    tmp_path: Path,
) -> None:
    """vue-tsc defers to generic discovery when only tsconfig.json exists.

    Args:
        tmp_path: Temporary directory holding only tsconfig.json.
    """
    (tmp_path / "tsconfig.json").write_text("{}")

    result = VueTscPlugin()._preferred_candidate_tsconfig(tmp_path)

    assert_that(result).is_none()


def test_preferred_candidate_tsconfig_tsc_always_none(tmp_path: Path) -> None:
    """Tsc never short-circuits discovery, even with tsconfig.app.json present.

    Args:
        tmp_path: Temporary directory holding both configs.
    """
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "tsconfig.app.json").write_text("{}")

    result = TscPlugin()._preferred_candidate_tsconfig(tmp_path)

    assert_that(result).is_none()


# =============================================================================
# Framework detection (tsc-only delta)
# =============================================================================


def test_tsc_defers_to_framework_checker(tmp_path: Path) -> None:
    """Tsc skips and recommends the framework checker for a Vue project.

    Args:
        tmp_path: Temporary directory holding a Vue project config.
    """
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "vue.config.js").write_text("module.exports = {};\n")
    ts_file = tmp_path / "main.ts"
    ts_file.write_text("const x: number = 1;\n")

    result = _run_check(
        plugin=TscPlugin(),
        paths=[str(ts_file)],
        subprocess_return=(True, ""),
    )

    assert_that(result.skipped).is_true()
    assert_that(result.output).contains("Vue project detected")
    assert_that(result.output).contains("vue-tsc")


def test_vue_tsc_has_no_framework_detection(tmp_path: Path) -> None:
    """vue-tsc does not defer to any framework checker.

    Args:
        tmp_path: Temporary directory holding a Vue project config.
    """
    result = VueTscPlugin()._detect_framework_project(tmp_path)

    assert_that(result).is_none()


# =============================================================================
# Shared dependency-error shaping
# =============================================================================


@pytest.mark.parametrize(
    ("plugin_factory", "file_name", "file_body"),
    [
        (TscPlugin, "main.ts", "import x from 'missing-pkg';\n"),
        (VueTscPlugin, "App.vue", "<template><div/></template>\n"),
    ],
)
def test_dependency_errors_share_output_shape(
    *,
    plugin_factory: type[TypeScriptCheckerPlugin],
    file_name: str,
    file_body: str,
    tmp_path: Path,
) -> None:
    """Both tools emit the same dependency-guidance block for missing modules.

    Args:
        plugin_factory: Concrete plugin class to instantiate.
        file_name: Name of the source file to create.
        file_body: Contents of the source file.
        tmp_path: Temporary directory for the source file.
    """
    src = tmp_path / file_name
    src.write_text(file_body)
    output = f"{src}(1,20): error TS2307: Cannot find module 'missing-pkg'."

    result = _run_check(
        plugin=plugin_factory(),
        paths=[str(src)],
        subprocess_return=(False, output),
    )

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Missing dependencies detected:")
    assert_that(result.output).contains("--auto-install")


# =============================================================================
# Per-tool error copy
# =============================================================================


def test_tsc_config_error_copy_mentions_skip_lib_check() -> None:
    """Tsc's config-error guidance includes the skip_lib_check hint."""
    copy = TscPlugin()._config_error_output("Cannot find module 'x'")

    assert_that(copy).starts_with("TypeScript configuration error:")
    assert_that(copy).contains("tsc:skip_lib_check=true")


def test_vue_tsc_config_error_copy_omits_skip_lib_check() -> None:
    """vue-tsc's config-error guidance omits the skip_lib_check hint."""
    copy = VueTscPlugin()._config_error_output("Cannot find module 'x'")

    assert_that(copy).starts_with("vue-tsc configuration error:")
    assert_that(copy).does_not_contain("skip_lib_check")


def test_not_found_copy_is_tool_specific() -> None:
    """Each tool reports its own install guidance when the binary is missing."""
    err = FileNotFoundError("no such file")
    tsc_copy = TscPlugin()._not_found_output(err)
    vue_copy = VueTscPlugin()._not_found_output(err)

    assert_that(tsc_copy).contains("TypeScript compiler not found")
    assert_that(tsc_copy).contains("typescript")
    assert_that(vue_copy).contains("vue-tsc not found")


# =============================================================================
# Timeout handling (shared)
# =============================================================================


@pytest.mark.parametrize(
    ("plugin_factory", "file_name", "file_body"),
    [
        (TscPlugin, "main.ts", "const x: number = 1;\n"),
        (VueTscPlugin, "App.vue", "<template><div/></template>\n"),
    ],
)
def test_timeout_marks_failure(
    *,
    plugin_factory: type[TypeScriptCheckerPlugin],
    file_name: str,
    file_body: str,
    tmp_path: Path,
) -> None:
    """A subprocess timeout is surfaced as a failing result for both tools.

    Args:
        plugin_factory: Concrete plugin class to instantiate.
        file_name: Name of the source file to create.
        file_body: Contents of the source file.
        tmp_path: Temporary directory for the source file.
    """
    src = tmp_path / file_name
    src.write_text(file_body)
    plugin = plugin_factory()

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["checker"], timeout=1),
        ):
            result = plugin.check([str(src)], {})

    assert_that(result.success).is_false()


# =============================================================================
# fix() delegation (shared method, per-tool message)
# =============================================================================


def test_tsc_fix_raises_with_tsc_message() -> None:
    """tsc.fix raises NotImplementedError referencing tsc."""
    with pytest.raises(NotImplementedError, match="Tsc cannot automatically fix"):
        TscPlugin().fix(paths=["src"], options={})


def test_vue_tsc_fix_raises_with_vue_message() -> None:
    """vue-tsc.fix raises NotImplementedError referencing vue-tsc."""
    with pytest.raises(NotImplementedError, match="vue-tsc cannot automatically fix"):
        VueTscPlugin().fix(paths=["src"], options={})
