"""Shared fixtures for AI review unit tests."""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from tests.unit.ai.review.review_fixtures import load_review_fixture

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SNAPSHOT_DELIM_RE = re.compile(r"---LINTRO_DIFF_SNAP_([a-f0-9]+)---")


def _diff_ref_from_bash_snapshot_script(*, script: str) -> str:
    """Extract the full ``git diff`` argument signature from a snapshot script."""
    for command in script.split(";"):
        tokens = shlex.split(command)
        if (
            not any(Path(token).name == "git" for token in tokens)
            or "diff" not in tokens
        ):
            continue
        diff_index = tokens.index("diff")
        index = diff_index + 1
        while index < len(tokens) and tokens[index].startswith("-"):
            index += 1
        if index >= len(tokens):
            continue
        args: list[str] = []
        while index < len(tokens):
            token = tokens[index]
            if token in {"--name-status", "--numstat"}:
                break
            args.append(token)
            index += 1
        if args:
            return " ".join(args)
    msg = f"could not parse git diff target from script: {script!r}"
    raise AssertionError(msg)


def _assert_review_subprocess_kwargs(*, kwargs: dict[str, object]) -> None:
    """Assert subprocess calls keep review output decoding stable."""
    expected = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "surrogateescape",
        "check": False,
    }
    for key, value in expected.items():
        if kwargs.get(key) != value:
            msg = f"expected subprocess kwarg {key}={value!r}, got {kwargs.get(key)!r}"
            raise AssertionError(msg)
    if "timeout" not in kwargs:
        msg = "expected subprocess timeout kwarg"
        raise AssertionError(msg)


class SubprocessMock:
    """Dispatch ``subprocess.run`` by argv with per-command response queues."""

    def __init__(self) -> None:
        """Initialize empty response queues."""
        self._queues: dict[tuple[str, ...], list[CompletedProcess[str]]] = defaultdict(
            list,
        )
        self._bash_snapshots: list[tuple[str, str, str, str]] = []

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

    def queue_bash_snapshot(
        self,
        *,
        diff_ref: str,
        unified: str = "",
        name_status: str = "",
        numstat: str = "",
    ) -> None:
        """Register stdout for the next combined ``bash -c`` diff collection."""
        self._bash_snapshots.append((diff_ref, unified, name_status, numstat))

    def __call__(
        self,
        args: list[str],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return the next queued response for ``args``."""
        _assert_review_subprocess_kwargs(kwargs=kwargs)
        normalized_args = [Path(args[0]).name, *args[1:]]
        if len(normalized_args) >= 3 and normalized_args[:2] == ["bash", "-c"]:
            script = args[2]
            requested_ref = _diff_ref_from_bash_snapshot_script(script=script)
            for index, (diff_ref, unified, name_status, numstat) in enumerate(
                self._bash_snapshots,
            ):
                if diff_ref == requested_ref:
                    del self._bash_snapshots[index]
                    match = _SNAPSHOT_DELIM_RE.search(script)
                    if match is None:
                        msg = f"could not parse snapshot delimiter from script: {script!r}"
                        raise AssertionError(msg)
                    delimiter = f"\n---LINTRO_DIFF_SNAP_{match.group(1)}---\n"
                    stdout = unified + delimiter + name_status + delimiter + numstat
                    return CompletedProcess(
                        args=["bash", "-c", script],
                        returncode=0,
                        stdout=stdout,
                        stderr="",
                    )

        key = tuple(normalized_args)
        if not self._queues[key]:
            if key == ("git", "rev-parse", "--show-toplevel"):
                return CompletedProcess(
                    args=list(args),
                    returncode=0,
                    stdout="/repo/root\n",
                    stderr="",
                )
            msg = f"unexpected subprocess call: {args!r}"
            raise AssertionError(msg)
        return self._queues[key].pop(0)


def queue_diff_snapshot(
    dispatcher: SubprocessMock,
    *,
    diff_ref: str,
    unified: str = "",
    name_status: str = "",
    numstat: str = "",
) -> None:
    """Register the combined stdout for a ``git diff`` collection."""
    dispatcher.queue_bash_snapshot(
        diff_ref=diff_ref,
        unified=unified,
        name_status=name_status,
        numstat=numstat,
    )


@pytest.fixture
def sample_unified_diff() -> str:
    """Return a multi-file unified diff for chunking tests."""
    return load_review_fixture("chunk_workflow_script_test.diff")


@pytest.fixture
def sample_review_context(sample_unified_diff: str) -> ReviewContext:
    """Return a review context built from the sample unified diff."""
    return ReviewContext(
        base_ref="abc123",
        head_ref="def456",
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/ci/run.sh",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/ci/test_run.bats",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="src/lib/math.py",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="tests/lib/test_math.py",
                status=ChangedFileStatus.MODIFIED,
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
@@ -1 +1,2 @@
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
            checklist_items=2,
            token_usage={"prompt": 1000, "completion": 200, "total": 1200},
            cost_estimate_usd=0.05,
            base_ref="main",
            head_ref="feature",
            timestamp="2026-06-24T10:00:00+00:00",
        ),
        summary="Merge with fixes.",
        checklist=(
            ChecklistAnswer(id=1, answer="yes", evidence="src/main.py:10"),
            ChecklistAnswer(id=2, answer="no", evidence="none"),
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
