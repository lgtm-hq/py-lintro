"""Integration tests for the buf tool definition.

These tests require buf to be installed and available in PATH. Fixtures are
staged into ``tmp_path`` because ``.lintro-ignore`` excludes ``test_samples/``
from discovery, so the committed sample cannot be linted in place.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.definitions.buf import BufPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("buf") is None,
    reason="buf not installed",
)

_VIOLATIONS = """\
syntax = "proto3";

package MyPackage;

message foo {
  string BadField = 1;
}
"""

_CLEAN = """\
syntax = "proto3";

package clean.v1;

message Thing {
  string name = 1;
}
"""

_UNFORMATTED = """\
syntax = "proto3";

package clean.v1;

message Thing {
  string name = 1;
    string other = 2;
}
"""

# A second, always-clean proto in a sibling package directory. Staging it
# alongside the target file makes ``tmp_path`` the common parent (and thus the
# buf module root), so directory-based rules like PACKAGE_DIRECTORY_MATCH see
# each package at a path matching its name. See buf-analysis.md for why the
# module root matters.
_ANCHOR = """\
syntax = "proto3";

package anchor.v1;

message Anchor {
  string id = 1;
}
"""


def _stage_module(tmp_path: Path, target_body: str) -> Path:
    """Stage a two-package buf module rooted at ``tmp_path``.

    Args:
        tmp_path: Root directory that becomes the buf module root.
        target_body: Proto source for the ``clean/v1/thing.proto`` file.

    Returns:
        Path to the staged target proto file.
    """
    anchor_dir = tmp_path / "anchor" / "v1"
    anchor_dir.mkdir(parents=True)
    (anchor_dir / "anchor.proto").write_text(_ANCHOR)

    target_dir = tmp_path / "clean" / "v1"
    target_dir.mkdir(parents=True)
    target = target_dir / "thing.proto"
    target.write_text(target_body)
    return target


def test_check_detects_lint_violations(tmp_path: Path) -> None:
    """Buf lint reports naming/package violations on a bad proto.

    Args:
        tmp_path: Temporary directory for the staged fixture.
    """
    proto = tmp_path / "bad.proto"
    proto.write_text(_VIOLATIONS)

    plugin = BufPlugin()
    result = plugin.check([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.issues).is_not_none()
    codes = {issue.code for issue in result.issues}  # type: ignore[union-attr]
    assert_that(codes).contains("MESSAGE_PASCAL_CASE")


def test_check_clean_but_valid_package(tmp_path: Path) -> None:
    """A well-named, well-formatted proto produces no issues.

    Args:
        tmp_path: Temporary directory for the staged fixture.
    """
    _stage_module(tmp_path, _CLEAN)

    plugin = BufPlugin()
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_detects_formatting(tmp_path: Path) -> None:
    """Buf format --diff flags an unformatted (but lint-clean) proto.

    Args:
        tmp_path: Temporary directory for the staged fixture.
    """
    _stage_module(tmp_path, _UNFORMATTED)

    plugin = BufPlugin()
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues).is_not_none()
    codes = {issue.code for issue in result.issues}  # type: ignore[union-attr]
    assert_that(codes).contains("FORMAT")


def test_fix_formats_in_place(tmp_path: Path) -> None:
    """Buf format --write rewrites the file and clears the FORMAT issue.

    Args:
        tmp_path: Temporary directory for the staged fixture.
    """
    _stage_module(tmp_path, _UNFORMATTED)

    plugin = BufPlugin()
    result = plugin.fix([str(tmp_path)], {})

    assert_that(result.fixed_issues_count).is_greater_than(0)
    # The file should now be lint- and format-clean.
    recheck = plugin.check([str(tmp_path)], {})
    assert_that(recheck.success).is_true()
