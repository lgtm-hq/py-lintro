"""Shared fixtures for AI review unit tests."""

from __future__ import annotations

from collections import defaultdict
from subprocess import CompletedProcess

import pytest

from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult


class SubprocessMock:
    """Dispatch ``subprocess.run`` by argv with per-command response queues."""

    def __init__(self) -> None:
        """Initialize empty response queues."""
        self._queues: dict[tuple[str, ...], list[CompletedProcess[str]]] = defaultdict(
            list,
        )

    def queue(
        self,
        argv: list[str],
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> None:
        """Register the next response for a subprocess argv list."""
        self._queues[tuple(argv)].append(
            CompletedProcess(
                args=argv,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            ),
        )

    def __call__(
        self,
        args: list[str],
        **_kwargs: object,
    ) -> CompletedProcess[str]:
        """Return the next queued response for ``args``."""
        key = tuple(args)
        if not self._queues[key]:
            msg = f"unexpected subprocess call: {args!r}"
            raise AssertionError(msg)
        return self._queues[key].pop(0)


@pytest.fixture
def sample_unified_diff() -> str:
    """Return a multi-file unified diff for chunking tests."""
    return """\
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 1111111..2222222 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1,3 +1,4 @@
 name: CI
+run: scripts/ci/run.sh
diff --git a/scripts/ci/run.sh b/scripts/ci/run.sh
index 1111111..2222222 100755
--- a/scripts/ci/run.sh
+++ b/scripts/ci/run.sh
@@ -1,2 +1,3 @@
 #!/usr/bin/env bash
+echo "running"
diff --git a/scripts/ci/test_run.bats b/scripts/ci/test_run.bats
index 1111111..2222222 100644
--- a/scripts/ci/test_run.bats
+++ b/scripts/ci/test_run.bats
@@ -1,2 +1,3 @@
 @test "run script" {
+  run scripts/ci/run.sh
 }
diff --git a/src/lib/math.py b/src/lib/math.py
index 1111111..2222222 100644
--- a/src/lib/math.py
+++ b/src/lib/math.py
@@ -1,2 +1,3 @@
 def add(a, b):
+    return a + b
diff --git a/tests/test_math.py b/tests/test_math.py
index 1111111..2222222 100644
--- a/tests/test_math.py
+++ b/tests/test_math.py
@@ -1,2 +1,3 @@
 def test_add():
+    assert add(1, 2) == 3
"""


@pytest.fixture
def sample_review_context(sample_unified_diff: str) -> ReviewContext:
    """Return a review context built from the sample unified diff."""
    return ReviewContext(
        base_ref="abc123",
        head_ref="def456",
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/ci/run.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/ci/test_run.bats",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="src/lib/math.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="tests/test_math.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=sample_unified_diff,
        pr_metadata=None,
    )


@pytest.fixture
def repetitive_unified_diff() -> str:
    """Return a diff with six identical file changes."""
    hunk = """\
diff --git a/pkg/item{idx}.py b/pkg/item{idx}.py
index 1111111..2222222 100644
--- a/pkg/item{idx}.py
+++ b/pkg/item{idx}.py
@@ -1,2 +1,3 @@
 value = 1
+value = 2
"""
    return "".join(hunk.format(idx=index) for index in range(6))


@pytest.fixture
def sample_review_result() -> ReviewResult:
    """Build a representative review result for display/output tests."""
    return ReviewResult(
        metadata=ReviewMetadata(
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            context_window=200_000,
            depth=2,
            chunks_total=2,
            chunks_current=2,
            files_reviewed=3,
            files_total=3,
            checklist_items=3,
            token_usage={"prompt": 1000, "completion": 200, "total": 1200},
            cost_estimate_usd=0.05,
            base_ref="main",
            head_ref="feature",
            timestamp="2026-06-24T10:00:00+00:00",
        ),
        summary="Merge with fixes.",
        checklist=(
            ChecklistAnswer(
                id=1,
                answer="yes",
                evidence="src/main.py:10",
                question="Does unknown status fail closed?",
            ),
            ChecklistAnswer(
                id=2,
                answer="no",
                evidence="none",
                question="Are access paths covered by tests?",
            ),
            ChecklistAnswer(
                id=3,
                answer="yes",
                evidence="docs/README.md:1",
                question="Is migration documented?",
            ),
        ),
        findings=(
            ReviewFinding(
                severity="P1",
                category="security",
                file="src/main.py",
                line=10,
                title="Fail-open default",
                description="Unknown status grants access",
                cause="else branch returns Active",
                fix="Default to Expired",
                confidence="high",
                checklist_ids=(1,),
            ),
            ReviewFinding(
                severity="P2",
                category="test-gap",
                file="tests/test_main.py",
                line=5,
                title="Missing access test",
                description="No test for unknown status",
                cause="Test gap",
                fix="Add unit test",
                confidence="medium",
                checklist_ids=(2,),
            ),
        ),
    )
