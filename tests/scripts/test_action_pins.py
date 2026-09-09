"""Tests for the derived action-pin helper (#2432).

The helper replaced hardcoded ``owner/repo@sha`` literals in the workflow
tests, so it is now the only thing standing between a bad pin and a green
suite. These tests drive it against synthetic workflow trees under ``tmp_path``
to prove it still fails on the regressions it exists to catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from tests.scripts._action_pins import (
    REPO_ROOT,
    action_pin,
    actions_used_in,
    iter_action_uses,
    pinned_action_shas,
    workflow_paths,
)

_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
_OTHER_SHA = "e14015d583714f6e62063499dc959a02595150a1"


def _write_workflow(*, root: Path, name: str, body: str) -> Path:
    """Write a synthetic workflow file into a fake repo root.

    Args:
        root: Fake repository root.
        name: Workflow file name.
        body: Workflow contents.

    Returns:
        The written path.
    """
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    path = workflows / name
    path.write_text(body, encoding="utf-8")
    return path


def _steps(*uses_lines: str) -> str:
    """Return a minimal workflow whose single job runs the given steps.

    Args:
        *uses_lines: Raw ``uses:`` values, comments included.

    Returns:
        A workflow document.
    """
    steps = "\n".join(f"      - uses: {line}" for line in uses_lines)
    return f"name: synthetic\non: push\njobs:\n  build:\n    steps:\n{steps}\n"


def test_pins_are_derived_from_the_workflow_files(tmp_path: Path) -> None:
    """A well-formed tree yields one SHA per action.

    Args:
        tmp_path: Pytest temporary directory.
    """
    _write_workflow(
        root=tmp_path,
        name="a.yml",
        body=_steps(f"actions/checkout@{_SHA} # v7.0.1"),
    )
    _write_workflow(
        root=tmp_path,
        name="b.yml",
        body=_steps(
            f"actions/checkout@{_SHA} # v7",
            f"step-security/harden-runner@{_OTHER_SHA} # v2.21.1",
        ),
    )

    assert_that(pinned_action_shas(root=tmp_path)).is_equal_to(
        {
            "actions/checkout": _SHA,
            "step-security/harden-runner": _OTHER_SHA,
        },
    )


def test_local_and_unpinned_references_are_ignored(tmp_path: Path) -> None:
    """Local ``./`` references have nothing to pin and must not be flagged.

    Args:
        tmp_path: Pytest temporary directory.
    """
    _write_workflow(
        root=tmp_path,
        name="a.yml",
        body=_steps(
            "./.github/actions/setup-env",
            "./.github/workflows/publish-npm.yml",
            f"actions/checkout@{_SHA} # v7.0.1",
        ),
    )

    assert_that([use.action for use in iter_action_uses(root=tmp_path)]).is_equal_to(
        ["actions/checkout"],
    )


def test_mismatched_sha_across_workflows_fails(tmp_path: Path) -> None:
    """Two workflows pinning one action differently is the #2423 regression.

    Args:
        tmp_path: Pytest temporary directory.
    """
    _write_workflow(
        root=tmp_path,
        name="a.yml",
        body=_steps(f"actions/checkout@{_SHA} # v7.0.1"),
    )
    _write_workflow(
        root=tmp_path,
        name="b.yml",
        body=_steps(f"actions/checkout@{_OTHER_SHA} # v7.0.1"),
    )

    with pytest.raises(AssertionError) as excinfo:
        pinned_action_shas(root=tmp_path)

    assert_that(str(excinfo.value)).contains("same commit SHA")


@pytest.mark.parametrize(
    "ref",
    ["v7", "main", "3d3c42e", f"{_SHA.upper()}"],
    ids=["tag", "branch", "short-sha", "uppercase-sha"],
)
def test_non_sha_reference_fails(*, ref: str, tmp_path: Path) -> None:
    """Anything but a lowercase 40-hex SHA is an unpinned action.

    Args:
        ref: The ref written after the ``@``.
        tmp_path: Pytest temporary directory.
    """
    _write_workflow(
        root=tmp_path,
        name="a.yml",
        body=_steps(f"actions/checkout@{ref} # v7.0.1"),
    )

    with pytest.raises(AssertionError) as excinfo:
        pinned_action_shas(root=tmp_path)

    assert_that(str(excinfo.value)).contains("40-character commit SHA")


@pytest.mark.parametrize(
    "comment",
    ["", "# pinned", "# see docs"],
    ids=["missing", "no-version", "prose"],
)
def test_missing_version_comment_fails(*, comment: str, tmp_path: Path) -> None:
    """A pin without a ``# vX.Y.Z`` comment is one Renovate cannot read.

    Args:
        comment: Trailing comment on the ``uses:`` line.
        tmp_path: Pytest temporary directory.
    """
    _write_workflow(
        root=tmp_path,
        name="a.yml",
        body=_steps(f"actions/checkout@{_SHA} {comment}".strip()),
    )

    with pytest.raises(AssertionError) as excinfo:
        pinned_action_shas(root=tmp_path)

    assert_that(str(excinfo.value)).contains("version comment")


def test_empty_tree_fails(tmp_path: Path) -> None:
    """Scanning a tree with no pins is a broken scan, not a clean result.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / ".github" / "workflows").mkdir(parents=True)

    with pytest.raises(AssertionError) as excinfo:
        pinned_action_shas(root=tmp_path)

    assert_that(str(excinfo.value)).contains("no third-party")


def test_action_pin_reports_unknown_actions(tmp_path: Path) -> None:
    """Asking for an action the tree never uses is a test bug, not a pass.

    Args:
        tmp_path: Pytest temporary directory.
    """
    _write_workflow(
        root=tmp_path,
        name="a.yml",
        body=_steps(f"actions/checkout@{_SHA} # v7.0.1"),
    )

    assert_that(action_pin("actions/checkout", root=tmp_path)).is_equal_to(
        f"actions/checkout@{_SHA}",
    )
    with pytest.raises(AssertionError):
        action_pin("actions/nope", root=tmp_path)


def test_repo_workflows_agree_on_every_action_pin() -> None:
    """The real repository satisfies the invariants the helper enforces."""
    pins = pinned_action_shas()

    assert_that(pins).contains_key("actions/checkout", "step-security/harden-runner")
    assert_that(action_pin("actions/checkout")).starts_with("actions/checkout@")


def test_workflow_paths_cover_workflows_and_composite_actions() -> None:
    """Composite actions are scanned too; they pin actions of their own."""
    relative = {path.relative_to(REPO_ROOT).parts[1] for path in workflow_paths()}

    assert_that(relative).contains("workflows", "actions")
    assert_that(
        actions_used_in(REPO_ROOT / ".github" / "workflows" / "ai-review.yml"),
    ).contains("actions/checkout")
