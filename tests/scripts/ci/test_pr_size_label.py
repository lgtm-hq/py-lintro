# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for PR size label classification (effective diff lines)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "pr-size-label.py"


@pytest.fixture(scope="module")
def pr_size_module() -> Any:
    """Load the hyphenated pr-size-label script once for the module."""
    spec = importlib.util.spec_from_file_location("pr_size_label", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclass slots require the module to be present in sys.modules during
    # class creation (importlib.exec_module alone is not enough).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _file(
    module: Any,
    *,
    path: str,
    additions: int,
    deletions: int = 0,
) -> Any:
    """Build a FileDiff via the loaded module's dataclass."""
    return module.FileDiff(path=path, additions=additions, deletions=deletions)


@pytest.mark.parametrize(
    ("effective", "expected"),
    [
        (0, "size:XS"),
        (20, "size:XS"),
        (20.0, "size:XS"),
        (20.1, "size:S"),
        (100, "size:S"),
        (100.1, "size:M"),
        (400, "size:M"),
        (400.1, "size:L"),
        (1000, "size:L"),
        (1000.1, "size:XL"),
        (5000, "size:XL"),
    ],
    ids=[
        "zero=XS",
        "boundary=XS",
        "boundary-float=XS",
        "just-over-XS=S",
        "boundary=S",
        "just-over-S=M",
        "boundary=M",
        "just-over-M=L",
        "boundary=L",
        "just-over-L=XL",
        "large=XL",
    ],
)
def test_size_label_for_lines_boundaries(
    *,
    pr_size_module: Any,
    effective: float,
    expected: str,
) -> None:
    """Threshold boundaries map to the expected size label."""
    assert_that(
        pr_size_module.size_label_for_lines(effective_lines=effective),
    ).is_equal_to(expected)


def test_mixed_src_and_tests_excludes_test_lines(*, pr_size_module: Any) -> None:
    """When source and tests both change, only source lines count."""
    files = [
        _file(pr_size_module, path="lintro/cli.py", additions=50, deletions=10),
        _file(pr_size_module, path="tests/unit/test_cli.py", additions=400),
        _file(pr_size_module, path="tests/unit/helpers.py", additions=100),
    ]
    effective = pr_size_module.compute_effective_lines(files=files)
    assert_that(effective).is_equal_to(60.0)
    assert_that(
        pr_size_module.size_label_for_lines(effective_lines=effective),
    ).is_equal_to("size:S")


def test_test_only_pr_counts_test_lines(*, pr_size_module: Any) -> None:
    """A PR that only touches tests counts those test lines."""
    files = [
        _file(pr_size_module, path="tests/unit/test_cli.py", additions=80),
        _file(pr_size_module, path="tests/unit/test_foo.py", additions=25),
    ]
    effective = pr_size_module.compute_effective_lines(files=files)
    assert_that(effective).is_equal_to(105.0)
    assert_that(
        pr_size_module.size_label_for_lines(effective_lines=effective),
    ).is_equal_to("size:M")


def test_test_filename_pattern_is_detected(*, pr_size_module: Any) -> None:
    """``test_*.py`` outside tests/ still counts as a test path."""
    assert_that(pr_size_module.is_test_path(path="lintro/test_helpers.py")).is_true()
    assert_that(pr_size_module.is_test_path(path="lintro/helpers.py")).is_false()


def test_lockfile_heavy_pr_is_excluded(*, pr_size_module: Any) -> None:
    """Lockfiles contribute zero effective lines."""
    files = [
        _file(pr_size_module, path="uv.lock", additions=5000),
        _file(pr_size_module, path="package-lock.json", additions=2000),
        _file(pr_size_module, path="lintro/cli.py", additions=5),
    ]
    effective = pr_size_module.compute_effective_lines(files=files)
    assert_that(effective).is_equal_to(5.0)
    assert_that(
        pr_size_module.size_label_for_lines(effective_lines=effective),
    ).is_equal_to("size:XS")


def test_generated_manifest_is_excluded(*, pr_size_module: Any) -> None:
    """Generated manifest.json is excluded from the effective count."""
    files = [
        _file(pr_size_module, path="lintro/tools/manifest.json", additions=900),
        _file(
            pr_size_module,
            path="scripts/ci/generate-tool-versions.py",
            additions=10,
        ),
    ]
    effective = pr_size_module.compute_effective_lines(files=files)
    assert_that(effective).is_equal_to(10.0)


def test_docs_weighted_half(*, pr_size_module: Any) -> None:
    """``docs/**`` lines are weighted at 0.5."""
    files = [
        _file(pr_size_module, path="docs/guide.md", additions=100),
        _file(pr_size_module, path="docs/nested/a.md", additions=40, deletions=10),
    ]
    # 100*0.5 + 50*0.5 = 75
    effective = pr_size_module.compute_effective_lines(files=files)
    assert_that(effective).is_equal_to(75.0)
    assert_that(
        pr_size_module.size_label_for_lines(effective_lines=effective),
    ).is_equal_to("size:S")


def test_docs_plus_tests_excludes_tests(*, pr_size_module: Any) -> None:
    """Docs count as non-test source, so mixed docs+tests exclude tests."""
    files = [
        _file(pr_size_module, path="docs/readme.md", additions=40),
        _file(pr_size_module, path="tests/unit/test_x.py", additions=500),
    ]
    # docs only: 40 * 0.5 = 20
    effective = pr_size_module.compute_effective_lines(files=files)
    assert_that(effective).is_equal_to(20.0)


def test_lockfile_only_with_tests_is_test_only(*, pr_size_module: Any) -> None:
    """Lockfiles alone do not make a PR 'mixed'; tests still count."""
    files = [
        _file(pr_size_module, path="uv.lock", additions=3000),
        _file(pr_size_module, path="tests/unit/test_x.py", additions=30),
    ]
    effective = pr_size_module.compute_effective_lines(files=files)
    assert_that(effective).is_equal_to(30.0)


def test_stale_size_label_removal(*, pr_size_module: Any) -> None:
    """Stale size labels are listed for removal; non-size labels are kept."""
    current = ["ci", "size:M", "size:XS", "enhancement"]
    stale = pr_size_module.stale_size_labels(
        current_labels=current,
        desired="size:S",
    )
    assert_that(stale).is_equal_to(["size:M", "size:XS"])
    to_add = pr_size_module.labels_to_add(current_labels=current, desired="size:S")
    assert_that(to_add).is_equal_to(["size:S"])


def test_desired_label_already_present_adds_nothing(*, pr_size_module: Any) -> None:
    """When the correct size label is present, add list is empty."""
    current = ["size:S", "ci"]
    assert_that(
        pr_size_module.stale_size_labels(current_labels=current, desired="size:S"),
    ).is_equal_to([])
    assert_that(
        pr_size_module.labels_to_add(current_labels=current, desired="size:S"),
    ).is_equal_to([])


def test_classify_and_plan_end_to_end(*, pr_size_module: Any) -> None:
    """classify_and_plan wires effective lines to add/remove lists."""
    files = [
        _file(pr_size_module, path="lintro/a.py", additions=250),
        _file(pr_size_module, path="tests/unit/test_a.py", additions=900),
    ]
    effective, desired, to_add, to_remove = pr_size_module.classify_and_plan(
        files=files,
        current_labels=["size:XS", "ci"],
    )
    assert_that(effective).is_equal_to(250.0)
    assert_that(desired).is_equal_to("size:M")
    assert_that(to_add).is_equal_to(["size:M"])
    assert_that(to_remove).is_equal_to(["size:XS"])


def test_parse_pr_files_payload(*, pr_size_module: Any) -> None:
    """Files API JSON maps into FileDiff rows; missing counts become 0."""
    rows = pr_size_module.parse_pr_files_payload(
        payload=[
            {"filename": "a.py", "additions": 3, "deletions": 1},
            {"filename": "b.py"},
            {"additions": 9},
        ],
    )
    assert_that(len(rows)).is_equal_to(2)
    assert_that(rows[0].path).is_equal_to("a.py")
    assert_that(rows[0].changed_lines).is_equal_to(4)
    assert_that(rows[1].path).is_equal_to("b.py")
    assert_that(rows[1].changed_lines).is_equal_to(0)


def test_decode_paginated_json_raises_clean_error(*, pr_size_module: Any) -> None:
    """Malformed paginated API text becomes RuntimeError, not raw JSON error."""
    with pytest.raises(RuntimeError, match="failed to decode GitHub API response"):
        pr_size_module._decode_paginated_json_objects(raw="not-json{")


def test_decode_paginated_json_rejects_scalar(*, pr_size_module: Any) -> None:
    """Bare scalar top-level JSON values fail loudly."""
    with pytest.raises(RuntimeError, match="unexpected GitHub API JSON value type"):
        pr_size_module._decode_paginated_json_objects(raw="42")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("uv.lock", True),
        ("apps/foo/uv.lock", True),
        ("Cargo.lock", True),
        ("lintro/cli.py", False),
    ],
)
def test_is_lockfile(*, pr_size_module: Any, path: str, expected: bool) -> None:
    """Lockfile detection uses the basename only."""
    assert_that(pr_size_module.is_lockfile(path=path)).is_equal_to(expected)
