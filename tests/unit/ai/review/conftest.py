"""Shared fixtures for AI review unit tests."""

from __future__ import annotations

from collections import defaultdict
from subprocess import CompletedProcess

import pytest

from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext


class SubprocessMock:
    """Dispatch ``subprocess.run`` by argv with per-command response queues."""

    def __init__(self) -> None:
        """Initialize empty response queues."""
        self._queues: dict[tuple[str, ...], list[CompletedProcess[str]]] = defaultdict(
            list
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
