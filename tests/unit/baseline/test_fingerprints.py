"""Fingerprint stability, multiplicity, and maintenance coverage."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.utils.fingerprint_baseline import (
    filter_issues,
    fingerprint,
    maintain_baseline,
    snapshot,
)


def test_identity_follows_line_shifts_and_clone_roots(
    tmp_path: Path,
    finding: RuffIssue,
) -> None:
    """Locations shift without changing the message identity or occurrence budget."""
    original = fingerprint(finding, tool="ruff", root=tmp_path)
    shifted = replace(finding, file=str(tmp_path / "clone/src/a.py"), line=99, column=8)
    relocated = fingerprint(shifted, tool="ruff", root=tmp_path / "clone")
    assert original is not None and relocated is not None
    assert_that(relocated.key).is_equal_to(original.key)
    assert_that(relocated.line).is_equal_to(99)
    assert_that(original.message_hash).is_equal_to(
        "41843c4a7550b9bac8ec8f73b206423978c2aa7893811d5bb7e9f25808c8ec6e",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"message": "Unused value 43"},
        {"code": "F402"},
        {"file": "src/b.py"},
    ],
)
def test_meaningful_changes_are_new(
    tmp_path: Path,
    finding: RuffIssue,
    change: dict[str, Any],
) -> None:
    """Message numbers, codes, and file names distinguish findings."""
    before = fingerprint(finding, tool="ruff", root=tmp_path)
    after = fingerprint(replace(finding, **change), tool="ruff", root=tmp_path)
    assert before is not None and after is not None
    assert_that(after.key).is_not_equal_to(before.key)


def test_occurrence_budget_cannot_hide_new_duplicates(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """One acknowledged occurrence hides exactly one shifted occurrence."""
    baseline = snapshot([dirty_result], root=tmp_path)
    issues = [replace(finding, line=10), replace(finding, line=11)]
    retained, count = filter_issues(
        issues,
        result=dirty_result,
        baseline=baseline,
        root=tmp_path,
    )
    assert_that(count).is_equal_to(1)
    assert_that(retained).is_equal_to([issues[1]])
    other_tool = replace(dirty_result, name="mypy")
    unmatched, count = filter_issues(
        issues,
        result=other_tool,
        baseline=baseline,
        root=tmp_path,
    )
    assert_that(count).is_zero()
    assert_that(unmatched).is_equal_to(issues)


def test_snapshot_is_sorted_and_does_not_store_messages(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """Repeated observations coalesce into deterministic counted fingerprints."""
    issues = [replace(finding, file="z.py"), finding, replace(finding, line=4)]
    result = replace(dirty_result, issues=issues, issues_count=3)
    baseline = snapshot([result], root=tmp_path)
    assert_that([e.file for e in baseline.entries]).is_equal_to(["src/a.py", "z.py"])
    assert_that(baseline.entries[0].count).is_equal_to(2)
    assert_that(baseline.entries[0].line).is_equal_to(4)
    assert_that(baseline.model_dump_json()).does_not_contain(finding.message)


@pytest.mark.parametrize(
    "state",
    [
        {"inspected_files": None},
        {"inspected_files": False},
        {"timed_out": True, "inspected_files": False, "success": False},
        {"parse_failures_count": 1},
        {"skipped": True, "skip_reason": "missing", "inspected_files": False},
        {"success": False},
        {"no_files": True, "inspected_files": False},
    ],
)
def test_uncertain_checks_cannot_remove_entries(
    tmp_path: Path,
    dirty_result: ToolResult,
    state: dict[str, Any],
) -> None:
    """Neither coverage nor a successful peer can authorize uncertain deletion."""
    previous = snapshot([dirty_result], root=tmp_path)
    result = ToolResult(
        name="ruff",
        success=True,
        inspected_files=True,
        inspected_paths=frozenset({str(tmp_path / "src/a.py")}),
    )
    uncertain = replace(result, **state)
    peer = replace(result, name="black")
    current = snapshot([uncertain, peer], root=tmp_path)
    updated = maintain_baseline(
        previous,
        current,
        results=[uncertain, peer],
        root=tmp_path,
        prune=True,
    )
    assert_that(updated.entries).is_equal_to(previous.entries)


def test_prune_requires_exact_coverage_and_retains_other_tools(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """Only the explicitly clean file for that tool may lose acknowledgements."""
    other = replace(dirty_result, name="mypy")
    unvisited = replace(dirty_result, issues=[replace(finding, file="unseen.py")])
    previous = snapshot([dirty_result, other, unvisited], root=tmp_path)
    clean = ToolResult(name="ruff", success=True, inspected_files=True)
    unknown = maintain_baseline(
        previous,
        snapshot([clean], root=tmp_path),
        results=[clean],
        root=tmp_path,
        prune=True,
    )
    assert_that(unknown.entries).is_equal_to(previous.entries)
    clean = replace(clean, inspected_paths=frozenset({str(tmp_path / "src/a.py")}))
    pruned = maintain_baseline(
        previous,
        snapshot([clean], root=tmp_path),
        results=[clean],
        root=tmp_path,
        prune=True,
    )
    assert_that([(e.tool, e.file) for e in pruned.entries]).contains_only(
        ("mypy", "src/a.py"),
        ("ruff", "unseen.py"),
    )


def test_update_preserves_unseen_budgets_and_prune_never_adds(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """Partial dirty observations cannot shrink budgets or acknowledge via prune."""
    previous = snapshot(
        [
            replace(dirty_result, issues=[finding, finding], issues_count=2),
        ],
        root=tmp_path,
    )
    changed = replace(finding, code="F999")
    result = replace(dirty_result, issues=[finding, changed], issues_count=2)
    current = snapshot([result], root=tmp_path)
    updated = maintain_baseline(
        previous,
        current,
        results=[result],
        root=tmp_path,
        prune=False,
    )
    pruned = maintain_baseline(
        previous,
        current,
        results=[result],
        root=tmp_path,
        prune=True,
    )
    assert_that([e.count for e in updated.entries]).is_equal_to([2, 1])
    assert_that(pruned.entries).is_equal_to(previous.entries)


def test_outside_paths_and_symlink_escapes_cannot_be_acknowledged(
    tmp_path: Path,
    finding: RuffIssue,
) -> None:
    """A baseline cannot suppress findings beyond the chosen workspace root."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    for path in ("../other.py", "escape/other.py", ""):
        assert_that(
            fingerprint(replace(finding, file=path), tool="ruff", root=root),
        ).is_none()


def test_exact_dirty_coverage_prunes_fixed_fingerprints_and_duplicate_budget(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """A completed lint verdict may retain findings while proving others fixed."""
    obsolete = replace(finding, code="F999")
    previous = snapshot(
        [
            replace(dirty_result, issues=[finding, finding, obsolete], issues_count=3),
        ],
        root=tmp_path,
    )
    covered = replace(
        dirty_result,
        inspected_paths=frozenset({str(tmp_path / "src/a.py")}),
    )
    current = snapshot([covered], root=tmp_path)
    for prune in (False, True):
        updated = maintain_baseline(
            previous,
            current,
            results=[covered],
            root=tmp_path,
            prune=prune,
        )
        assert_that(updated.entries).is_length(1)
        assert_that(updated.entries[0].count).is_equal_to(1)
        assert_that(updated.entries[0].code).is_equal_to("F401")


def test_rule_exclusions_retain_uncovered_entries(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
) -> None:
    """Lint discovery alone must not remove a separately configured FORMAT entry."""
    previous = snapshot(
        [
            replace(
                dirty_result,
                issues=[finding, replace(finding, code="FORMAT")],
                issues_count=2,
            ),
        ],
        root=tmp_path,
    )
    result = ToolResult(
        name="ruff",
        success=True,
        inspected_files=True,
        inspected_paths=frozenset({str(tmp_path / "src/a.py")}),
        uninspected_codes=frozenset({"FORMAT"}),
    )
    updated = maintain_baseline(
        previous,
        snapshot([result], root=tmp_path),
        results=[result],
        root=tmp_path,
        prune=True,
    )
    assert_that([entry.code for entry in updated.entries]).is_equal_to(["FORMAT"])
