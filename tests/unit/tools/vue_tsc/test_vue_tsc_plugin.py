"""Unit tests for VueTscPlugin temp tsconfig functionality."""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

from lintro.tools.definitions.vue_tsc import VueTscPlugin

# =============================================================================
# Tests for JSONC tsconfig parsing (issue #570)
# =============================================================================


def test_create_temp_tsconfig_preserves_type_roots_from_jsonc_base(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Verify typeRoots are preserved when base tsconfig uses JSONC features.

    This is the primary scenario from issue #570: a tsconfig.json with
    comments and trailing commas should still have its typeRoots read
    and propagated into the temporary tsconfig.

    Args:
        vue_tsc_plugin: Plugin instance fixture.
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

    temp_path = vue_tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["src/App.vue"],
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
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Verify no typeRoots are added when the base config has none.

    Args:
        vue_tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text('{"compilerOptions": {"strict": true}}')

    temp_path = vue_tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["src/App.vue"],
        cwd=tmp_path,
    )

    try:
        content = json.loads(temp_path.read_text())
        assert_that(content["compilerOptions"]).does_not_contain_key("typeRoots")
    finally:
        temp_path.unlink(missing_ok=True)


def test_create_temp_tsconfig_ignores_non_list_type_roots(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Verify malformed typeRoots (non-list) are safely ignored.

    Args:
        vue_tsc_plugin: Plugin instance fixture.
        tmp_path: Pytest temporary directory.
    """
    base_tsconfig = tmp_path / "tsconfig.json"
    base_tsconfig.write_text(
        '{"compilerOptions": {"typeRoots": "not-a-list"}}',
    )

    temp_path = vue_tsc_plugin._create_temp_tsconfig(
        base_tsconfig=base_tsconfig,
        files=["src/App.vue"],
        cwd=tmp_path,
    )

    try:
        content = json.loads(temp_path.read_text())
        assert_that(content["compilerOptions"]).does_not_contain_key("typeRoots")
    finally:
        temp_path.unlink(missing_ok=True)
