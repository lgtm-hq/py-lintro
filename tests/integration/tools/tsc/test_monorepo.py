"""Integration tests for tsc monorepo and tsconfig scoping support.

Tests issues #851, #803, #805:
- Respect tsconfig.json include/exclude/files scoping
- Multi-project discovery via references and tree walking
- Per-project framework detection
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from tests.integration.tools.tsc.conftest import tsc_is_available

pytestmark = pytest.mark.skipif(
    not tsc_is_available(),
    reason="tsc not available",
)


def _write_json(path: Path, content: dict[str, Any]) -> None:
    """Write JSON content to a file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")


def _write_ts(path: Path, content: str) -> None:
    """Write TypeScript content to a file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# =============================================================================
# Issue #851 — Respect tsconfig include/exclude
# =============================================================================


def test_excluded_file_not_checked(tmp_path: Path) -> None:
    """Files excluded by tsconfig include are not type-checked.

    Regression test for issue #851: vitest.config.ts was force-included
    despite being excluded from the project's tsconfig.json.

    Args:
        tmp_path: Temporary directory for the test project.
    """
    from lintro.tools.definitions.tsc import TscPlugin

    # Project tsconfig scopes to src/ only
    _write_json(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {"strict": True, "noEmit": True},
            "include": ["src/**/*.ts"],
        },
    )

    # Clean file inside scope
    _write_ts(
        tmp_path / "src" / "app.ts",
        "export const greeting: string = 'hello';\n",
    )

    # File with type error OUTSIDE scope — should NOT be checked
    _write_ts(
        tmp_path / "config.ts",
        "const x: number = 'not a number';\n",
    )

    plugin = TscPlugin()
    result = plugin.check([str(tmp_path)], {})

    # Should pass because config.ts is outside the tsconfig include scope
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


# =============================================================================
# Multi-project support (#803, #805)
# =============================================================================


def test_monorepo_with_references(tmp_path: Path) -> None:
    """Monorepo with project references checks each sub-project.

    Args:
        tmp_path: Temporary directory for the test project.
    """
    from lintro.tools.definitions.tsc import TscPlugin

    # Root tsconfig with references
    _write_json(
        tmp_path / "tsconfig.json",
        {
            "references": [
                {"path": "./packages/clean"},
                {"path": "./packages/errors"},
            ],
        },
    )

    # Clean sub-project
    _write_json(
        tmp_path / "packages" / "clean" / "tsconfig.json",
        {
            "compilerOptions": {"strict": True, "noEmit": True, "composite": True},
            "include": ["src/**/*.ts"],
        },
    )
    _write_ts(
        tmp_path / "packages" / "clean" / "src" / "index.ts",
        "export const value: number = 42;\n",
    )

    # Sub-project with errors
    _write_json(
        tmp_path / "packages" / "errors" / "tsconfig.json",
        {
            "compilerOptions": {"strict": True, "noEmit": True, "composite": True},
            "include": ["src/**/*.ts"],
        },
    )
    _write_ts(
        tmp_path / "packages" / "errors" / "src" / "broken.ts",
        "const x: number = 'not a number';\n",
    )

    plugin = TscPlugin()
    result = plugin.check([str(tmp_path)], {})

    # Should fail because the errors sub-project has type errors
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_no_root_tsconfig_discovers_subdirs(tmp_path: Path) -> None:
    """Sub-project tsconfigs are discovered without a root tsconfig.

    Args:
        tmp_path: Temporary directory for the test project.
    """
    from lintro.tools.definitions.tsc import TscPlugin

    # No root tsconfig — only sub-projects
    _write_json(
        tmp_path / "packages" / "lib" / "tsconfig.json",
        {
            "compilerOptions": {"strict": True, "noEmit": True},
            "include": ["src/**/*.ts"],
        },
    )
    _write_ts(
        tmp_path / "packages" / "lib" / "src" / "utils.ts",
        "export const add = (a: number, b: number): number => a + b;\n",
    )

    plugin = TscPlugin()
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()


def test_backward_compat_no_tsconfig(tmp_path: Path) -> None:
    """Project with no tsconfig still works (files passed directly).

    Args:
        tmp_path: Temporary directory for the test project.
    """
    from lintro.tools.definitions.tsc import TscPlugin

    _write_ts(
        tmp_path / "app.ts",
        "const x: number = 42;\n",
    )

    plugin = TscPlugin()
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
