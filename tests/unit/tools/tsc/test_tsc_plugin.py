"""Unit tests for TscPlugin temp tsconfig functionality."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.definitions.tsc import TscPlugin

# =============================================================================
# Tests for TscPlugin._find_tsconfig method
# =============================================================================


def test_find_tsconfig_finds_tsconfig_in_cwd(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify _find_tsconfig finds tsconfig.json in working directory.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text("{}")

    result = tsc_plugin._find_tsconfig(tmp_path)

    assert_that(result).is_equal_to(tsconfig)


def test_find_tsconfig_returns_none_when_no_tsconfig(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify _find_tsconfig returns None when no tsconfig.json exists.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    result = tsc_plugin._find_tsconfig(tmp_path)

    assert_that(result).is_none()


def test_find_tsconfig_uses_explicit_project_option(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify _find_tsconfig uses explicit project option over auto-discovery.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    # Create both default and custom tsconfig
    default_tsconfig = tmp_path / "tsconfig.json"
    default_tsconfig.write_text("{}")

    custom_tsconfig = tmp_path / "tsconfig.build.json"
    custom_tsconfig.write_text("{}")

    tsc_plugin.set_options(project="tsconfig.build.json")
    result = tsc_plugin._find_tsconfig(tmp_path)

    assert_that(result).is_equal_to(custom_tsconfig)


# =============================================================================
# Tests for TscPlugin._create_temp_tsconfig method
# =============================================================================


def test_create_temp_tsconfig_creates_file_with_extends(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify temp tsconfig extends the base config.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text('{"compilerOptions": {"strict": true}}')

    temp_path = tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["src/file.ts"],
        cwd=tmp_path,
    )

    try:
        assert_that(temp_path.exists()).is_true()

        content = json.loads(temp_path.read_text())
        assert_that(content["extends"]).is_equal_to(str(base_tsconfig.resolve()))
    finally:
        temp_path.unlink(missing_ok=True)


def test_create_temp_tsconfig_includes_specified_files(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify temp tsconfig includes only specified files.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text("{}")

    files = ["src/a.ts", "src/b.ts", "lib/c.ts"]
    temp_path = tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=files,
        cwd=tmp_path,
    )

    try:
        content = json.loads(temp_path.read_text())
        expected_files = [str((tmp_path / f).resolve()) for f in files]
        assert_that(content["include"]).is_equal_to(expected_files)
        assert_that(content["exclude"]).is_equal_to([])
    finally:
        temp_path.unlink(missing_ok=True)


def test_create_temp_tsconfig_sets_no_emit(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify temp tsconfig sets noEmit compiler option.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text("{}")

    temp_path = tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["file.ts"],
        cwd=tmp_path,
    )

    try:
        content = json.loads(temp_path.read_text())
        assert_that(content["compilerOptions"]["noEmit"]).is_true()
    finally:
        temp_path.unlink(missing_ok=True)


def test_create_temp_tsconfig_file_created_next_to_base(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify temp tsconfig is created next to the base tsconfig.

    Placing the temp file in the project tree allows TypeScript to resolve
    types entries by walking up from the tsconfig location to node_modules.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text("{}")

    temp_path = tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["file.ts"],
        cwd=tmp_path,
    )

    try:
        assert_that(temp_path.parent).is_equal_to(tmp_path)
        assert_that(temp_path.name).starts_with(".lintro-tsc-")
        assert_that(temp_path.name).ends_with(".json")
    finally:
        temp_path.unlink(missing_ok=True)


def test_create_temp_tsconfig_falls_back_to_system_temp_with_typeroots(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify fallback to system temp dir injects typeRoots.

    When the project directory is read-only (e.g. Docker volume mount),
    the temp file falls back to the system temp dir and injects explicit
    typeRoots so TypeScript can still resolve type packages.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    import tempfile
    from typing import Any
    from unittest.mock import patch

    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text("{}")

    original_mkstemp = tempfile.mkstemp
    call_count = 0

    def mock_mkstemp(**kwargs: Any) -> tuple[int, str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call (project dir) fails
            raise OSError("Read-only filesystem")
        # Second call (system temp dir) succeeds
        return original_mkstemp(**kwargs)

    with patch("tempfile.mkstemp", side_effect=mock_mkstemp):
        temp_path = tsc_plugin._create_temp_tsconfig(
            base_tsconfig=base_tsconfig,
            files=["file.ts"],
            cwd=tmp_path,
        )

    try:
        system_temp = Path(tempfile.gettempdir())
        assert_that(temp_path.parent).is_equal_to(system_temp)

        content = json.loads(temp_path.read_text())
        expected_type_roots = [str(tmp_path / "node_modules" / "@types")]
        assert_that(content["compilerOptions"]["typeRoots"]).is_equal_to(
            expected_type_roots,
        )
    finally:
        temp_path.unlink(missing_ok=True)


def test_create_temp_tsconfig_fallback_preserves_custom_typeroots(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify fallback merges existing typeRoots from base tsconfig.

    When the base tsconfig has custom typeRoots, the fallback should
    resolve them relative to the base tsconfig directory and include
    the default node_modules/@types path.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    import tempfile
    from typing import Any
    from unittest.mock import patch

    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "typeRoots": ["./custom-types", "./other-types"],
                },
            },
        ),
    )

    original_mkstemp = tempfile.mkstemp
    call_count = 0

    def mock_mkstemp(**kwargs: Any) -> tuple[int, str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("Read-only filesystem")
        return original_mkstemp(**kwargs)

    with patch("tempfile.mkstemp", side_effect=mock_mkstemp):
        temp_path = tsc_plugin._create_temp_tsconfig(
            base_tsconfig=base_tsconfig,
            files=["file.ts"],
            cwd=tmp_path,
        )

    try:
        content = json.loads(temp_path.read_text())
        type_roots = content["compilerOptions"]["typeRoots"]
        # Custom roots resolved to absolute paths
        assert_that(type_roots).contains(
            str((tmp_path / "custom-types").resolve()),
        )
        assert_that(type_roots).contains(
            str((tmp_path / "other-types").resolve()),
        )
        # Default root also included
        assert_that(type_roots).contains(
            str(tmp_path / "node_modules" / "@types"),
        )
    finally:
        temp_path.unlink(missing_ok=True)


# =============================================================================
# Tests for TscPlugin.set_options validation
# =============================================================================


def test_set_options_validates_use_project_files_type(
    tsc_plugin: TscPlugin,
) -> None:
    """Verify set_options rejects non-boolean use_project_files.

    Args:
        tsc_plugin: Plugin instance fixture.
    """
    with pytest.raises(ValueError, match="use_project_files must be a boolean"):
        tsc_plugin.set_options(
            use_project_files="true",  # type: ignore[arg-type]  # Intentional wrong type
        )


def test_set_options_accepts_valid_use_project_files(
    tsc_plugin: TscPlugin,
) -> None:
    """Verify set_options accepts boolean use_project_files.

    Args:
        tsc_plugin: Plugin instance fixture.
    """
    tsc_plugin.set_options(use_project_files=True)
    assert_that(tsc_plugin.options.get("use_project_files")).is_true()

    tsc_plugin.set_options(use_project_files=False)
    assert_that(tsc_plugin.options.get("use_project_files")).is_false()


# =============================================================================
# Tests for TscPlugin default option values
# =============================================================================


def test_default_options_use_project_files_defaults_to_false(
    tsc_plugin: TscPlugin,
) -> None:
    """Verify use_project_files defaults to False for lintro-style targeting.

    Args:
        tsc_plugin: Plugin instance fixture.
    """
    default_options = tsc_plugin.definition.default_options
    assert_that(default_options.get("use_project_files")).is_false()


# =============================================================================
# Tests for TscPlugin._detect_framework_project method
# =============================================================================


@pytest.mark.parametrize(
    ("config_file", "expected_framework", "expected_tool"),
    [
        pytest.param(
            "astro.config.mjs",
            "Astro",
            "astro-check",
            id="astro_mjs",
        ),
        pytest.param(
            "astro.config.ts",
            "Astro",
            "astro-check",
            id="astro_ts",
        ),
        pytest.param(
            "astro.config.js",
            "Astro",
            "astro-check",
            id="astro_js",
        ),
        pytest.param(
            "svelte.config.js",
            "Svelte",
            "svelte-check",
            id="svelte_js",
        ),
        pytest.param(
            "svelte.config.ts",
            "Svelte",
            "svelte-check",
            id="svelte_ts",
        ),
        pytest.param(
            "vue.config.js",
            "Vue",
            "vue-tsc",
            id="vue_js",
        ),
        pytest.param(
            "vue.config.ts",
            "Vue",
            "vue-tsc",
            id="vue_ts",
        ),
    ],
)
def test_detect_framework_project(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
    config_file: str,
    expected_framework: str,
    expected_tool: str,
) -> None:
    """Verify framework detection identifies projects by config file.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
        config_file: Name of the framework config file to create.
        expected_framework: Expected framework name.
        expected_tool: Expected recommended tool name.
    """
    (tmp_path / config_file).write_text("export default {};")

    result = tsc_plugin._detect_framework_project(tmp_path)

    assert_that(result).is_not_none()
    assert result is not None
    framework_name, tool_name = result
    assert_that(framework_name).is_equal_to(expected_framework)
    assert_that(tool_name).is_equal_to(expected_tool)


def test_detect_framework_project_returns_none_for_plain_ts(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify framework detection returns None for plain TypeScript projects.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "tsconfig.json").write_text("{}")

    result = tsc_plugin._detect_framework_project(tmp_path)

    assert_that(result).is_none()


# =============================================================================
# Tests for JSONC tsconfig parsing (issue #570)
# =============================================================================


def test_create_temp_tsconfig_preserves_type_roots_from_jsonc_base(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify typeRoots are preserved when base tsconfig uses JSONC features.

    This is the primary scenario from issue #570: a tsconfig.json with
    comments and trailing commas should still have its typeRoots read
    and propagated into the temporary tsconfig.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text(
        """{
  // TypeScript config with JSONC features
  "compilerOptions": {
    "strict": true,
    /* Custom type roots for this project */
    "typeRoots": [
      "./custom-types",
      "./node_modules/@types",
    ],
  },
}""",
    )

    temp_path = tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["src/file.ts"],
        cwd=tmp_path,
    )

    try:
        content = json.loads(temp_path.read_text())
        # typeRoots should be resolved to absolute paths
        type_roots = content["compilerOptions"]["typeRoots"]
        assert_that(type_roots).is_length(2)
        assert_that(type_roots[0]).ends_with("custom-types")
        assert_that(type_roots[1]).ends_with("node_modules/@types")
    finally:
        temp_path.unlink(missing_ok=True)


def test_create_temp_tsconfig_no_type_roots_when_base_has_none(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify no typeRoots are added when the base config has none.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text('{"compilerOptions": {"strict": true}}')

    temp_path = tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["src/file.ts"],
        cwd=tmp_path,
    )

    try:
        content = json.loads(temp_path.read_text())
        assert_that(content["compilerOptions"]).does_not_contain_key("typeRoots")
    finally:
        temp_path.unlink(missing_ok=True)


def test_create_temp_tsconfig_ignores_non_list_type_roots(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Verify malformed typeRoots (non-list) are safely ignored.

    Args:
        tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text(
        '{"compilerOptions": {"typeRoots": "not-a-list"}}',
    )

    temp_path = tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["src/file.ts"],
        cwd=tmp_path,
    )

    try:
        content = json.loads(temp_path.read_text())
        assert_that(content["compilerOptions"]).does_not_contain_key("typeRoots")
    finally:
        temp_path.unlink(missing_ok=True)
