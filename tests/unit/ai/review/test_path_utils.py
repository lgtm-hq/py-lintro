"""Tests for review path pairing heuristics."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.path_utils import (
    is_e2e_test_path,
    is_test_path,
    matches_test_for_source,
)


def test_is_test_path_recognizes_common_test_layouts() -> None:
    """Test path detection covers directories, spec files, and BATS scripts."""
    assert_that(is_test_path("tests/test_main.py")).is_true()
    assert_that(is_test_path("__tests__/foo.test.ts")).is_true()
    assert_that(is_test_path("src/__tests__/button.test.tsx")).is_true()
    assert_that(is_test_path("src/button.spec.ts")).is_true()
    assert_that(is_test_path("test_foo.py")).is_true()
    assert_that(is_test_path("foo_test.js")).is_true()
    assert_that(is_test_path("tests/run.bats")).is_true()
    assert_that(is_test_path("src/button.tsx")).is_false()


def test_is_e2e_test_path_recognizes_e2e_directories_and_names() -> None:
    """E2E detection covers playwright-tests dirs and common filename markers."""
    assert_that(is_e2e_test_path("tests/e2e/login.spec.ts")).is_true()
    assert_that(is_e2e_test_path("playwright-tests/global-setup.ts")).is_true()
    assert_that(is_e2e_test_path("login.e2e-spec.ts")).is_true()
    assert_that(is_e2e_test_path("checkout.e2e_test.ts")).is_true()
    assert_that(is_e2e_test_path("tests/unit/service.test.ts")).is_false()
    assert_that(is_e2e_test_path("integrations/playwright/client.ts")).is_false()


def test_matches_test_for_source_pairs_dot_style_tests_across_directories() -> None:
    """Dot-style tests under tests/ pair with src/ sources."""
    assert_that(
        matches_test_for_source(
            test_path="tests/foo.test.ts",
            source_stem="foo",
            source_path="src/foo.ts",
        ),
    ).is_true()


def test_matches_test_for_source_pairs_python_underscore_tests() -> None:
    """Python underscore test files pair with source modules."""
    assert_that(
        matches_test_for_source(
            test_path="tests/foo_test.py",
            source_stem="foo",
            source_path="src/foo.py",
        ),
    ).is_true()


def test_matches_test_for_source_rejects_loose_substring_pairs() -> None:
    """Test paths must explicitly pair with a source stem, not substring-match."""
    assert_that(
        matches_test_for_source(test_path="tests/test_foobar.py", source_stem="foo"),
    ).is_false()
    assert_that(
        matches_test_for_source(test_path="tests/test_foo.py", source_stem="foo"),
    ).is_true()


def test_matches_test_for_source_rejects_src_prefix_false_positives() -> None:
    """Sources outside the canonical src/ tree do not mirror-match tests/."""
    assert_that(
        matches_test_for_source(
            test_path="tests/foo.test.ts",
            source_stem="foo",
            source_path="src2/foo.ts",
        ),
    ).is_false()


def test_matches_test_for_source_rejects_nested_tests_api_src2_collision() -> None:
    """Nested tests/api/ pairs with src/api only, not src2/api suffix collisions."""
    assert_that(
        matches_test_for_source(
            test_path="tests/api/foo.test.ts",
            source_stem="foo",
            source_path="src2/api/foo.ts",
        ),
    ).is_false()
    assert_that(
        matches_test_for_source(
            test_path="tests/api/foo.test.ts",
            source_stem="foo",
            source_path="src/api/foo.ts",
        ),
    ).is_true()


def test_matches_test_for_source_rejects_nested_tests_lib_suffix_collision() -> None:
    """Single-segment tests/<dir>/ mirrors src/<dir> only, not lib/<dir>."""
    assert_that(
        matches_test_for_source(
            test_path="tests/api/foo.test.ts",
            source_stem="foo",
            source_path="lib/api/foo.ts",
        ),
    ).is_false()


def test_matches_test_for_source_root_tests_only_pairs_direct_src_layout() -> None:
    """Root tests/ pairs with src/<file> only, not nested src/** modules."""
    assert_that(
        matches_test_for_source(
            test_path="tests/foo.test.ts",
            source_stem="foo",
            source_path="src/foo.ts",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="tests/foo.test.ts",
            source_stem="foo",
            source_path="src/components/foo.ts",
        ),
    ).is_false()


def test_matches_test_for_source_pairs_tests_unit_mirror_layout() -> None:
    """tests/unit mirrors pair with matching package source directories."""
    assert_that(
        matches_test_for_source(
            test_path="tests/unit/ai/review/test_glob_utils.py",
            source_stem="glob_utils",
            source_path="lintro/ai/review/glob_utils.py",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="tests/unit/ai/review/test_glob_utils.py",
            source_stem="glob_utils",
            source_path="lintro/other/glob_utils.py",
        ),
    ).is_false()


def test_matches_test_for_source_pairs_bare_tests_unit_with_src() -> None:
    """Bare tests/unit files pair with src/ sources when no package mirror exists."""
    assert_that(
        matches_test_for_source(
            test_path="tests/unit/test_foo.py",
            source_stem="foo",
            source_path="src/foo.py",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="tests/integration/test_foo.py",
            source_stem="foo",
            source_path="src/foo.py",
        ),
    ).is_true()


def test_matches_test_for_source_pairs_tests_unit_with_src_mirror() -> None:
    """Bare tests/unit files pair with src/ mirrors, not repo-root siblings."""
    assert_that(
        matches_test_for_source(
            test_path="tests/unit/test_cli.py",
            source_stem="cli",
            source_path="src/cli.py",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="tests/unit/test_cli.py",
            source_stem="cli",
            source_path="cli.py",
        ),
    ).is_false()
    assert_that(
        matches_test_for_source(
            test_path="tests/unit/nested/test_cli.py",
            source_stem="cli",
            source_path="cli.py",
        ),
    ).is_false()


def test_matches_test_for_source_rejects_unrelated_package_mirrors() -> None:
    """tests/pkg and src/pkg pair only when the package segment matches."""
    assert_that(
        matches_test_for_source(
            test_path="pkg_a/tests/foo.test.ts",
            source_stem="foo",
            source_path="pkg_b/src/foo.ts",
        ),
    ).is_false()
    assert_that(
        matches_test_for_source(
            test_path="pkg_a/tests/foo.test.ts",
            source_stem="foo",
            source_path="pkg_a/src/foo.ts",
        ),
    ).is_true()


def test_matches_test_for_source_rejects_directory_suffix_collisions() -> None:
    """Directory comparisons use path parts, not raw string suffixes."""
    assert_that(
        matches_test_for_source(
            test_path="pkg/tests/test_foo.py",
            source_stem="foo",
            source_path="other/pkg/foo.py",
        ),
    ).is_false()
    assert_that(
        matches_test_for_source(
            test_path="src/foo.test.ts",
            source_stem="foo",
            source_path="pkg/src/foo.ts",
        ),
    ).is_false()
    assert_that(
        matches_test_for_source(
            test_path="pkg/src/foo.test.ts",
            source_stem="foo",
            source_path="src/foo.ts",
        ),
    ).is_false()


def test_matches_test_for_source_pairs_pkg_tests_unit_with_src() -> None:
    """Package tests/unit mirrors pair with matching package src/ directories."""
    assert_that(
        matches_test_for_source(
            test_path="pkg/tests/unit/test_foo.py",
            source_stem="foo",
            source_path="pkg/src/foo.py",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="pkg/tests/unit/test_foo.py",
            source_stem="foo",
            source_path="other/src/foo.py",
        ),
    ).is_false()


def test_matches_test_for_source_pairs_pkg_tests_integration_with_src() -> None:
    """Package tests/integration mirrors pair with matching package src/ directories."""
    assert_that(
        matches_test_for_source(
            test_path="pkg/tests/integration/test_foo.py",
            source_stem="foo",
            source_path="pkg/src/foo.py",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="pkg/__tests__/integration/test_foo.py",
            source_stem="foo",
            source_path="pkg/src/foo.py",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="packages/foo/tests/unit/api/test_bar.py",
            source_stem="bar",
            source_path="packages/foo/src/api/bar.py",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="packages/foo/tests/api/test_bar.py",
            source_stem="bar",
            source_path="packages/foo/src/api/bar.py",
        ),
    ).is_true()


def test_matches_test_for_source_pairs_colocated_root_modules() -> None:
    """Root-level source and test files in the same directory pair together."""
    assert_that(
        matches_test_for_source(
            test_path="test_foo.py",
            source_stem="foo",
            source_path="foo.py",
        ),
    ).is_true()


def test_matches_test_for_source_rejects_root_module_false_positives() -> None:
    """Root-level sources do not pair with unrelated nested package tests."""
    assert_that(
        matches_test_for_source(
            test_path="pkg/tests/test_foo.py",
            source_stem="foo",
            source_path="foo.py",
        ),
    ).is_false()
    assert_that(
        matches_test_for_source(
            test_path="tests/test_foo.py",
            source_stem="foo",
            source_path="foo.py",
        ),
    ).is_true()


def test_matches_test_for_source_supports_plain_ts_and_js() -> None:
    """Plain TypeScript and JavaScript test naming patterns are recognized."""
    assert_that(
        matches_test_for_source(
            test_path="tests/test_foo.ts",
            source_stem="foo",
            source_path="src/foo.ts",
        ),
    ).is_true()
    assert_that(
        matches_test_for_source(
            test_path="tests/foo_test.js",
            source_stem="foo",
            source_path="src/foo.js",
        ),
    ).is_true()


def test_matches_test_for_source_pairs_plain_bats_files() -> None:
    """Plain stem.bats files pair with shell sources."""
    assert_that(
        matches_test_for_source(
            test_path="tests/run.bats",
            source_stem="run",
            source_path="run.sh",
        ),
    ).is_true()


def test_matches_test_for_source_pairs___tests___mirror_layout() -> None:
    """__tests__ mirror layouts pair with src/ sources."""
    assert_that(
        matches_test_for_source(
            test_path="__tests__/foo.test.ts",
            source_stem="foo",
            source_path="src/foo.ts",
        ),
    ).is_true()


def test_matches_test_for_source_pairs_nested_src___tests___layout() -> None:
    """Colocated __tests__ directories under src/ pair with sibling sources."""
    assert_that(
        matches_test_for_source(
            test_path="src/components/__tests__/foo.test.ts",
            source_stem="foo",
            source_path="src/components/foo.ts",
        ),
    ).is_true()
