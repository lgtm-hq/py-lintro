"""Shared fixtures for AI review unit tests."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from subprocess import (
    CompletedProcess,
)  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False

import pytest

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.verification_outcome import VerificationSummary
from lintro.ai.review.question_pass import RunQuestions
from lintro.ai.review.verification import VerificationPass
from tests.unit.ai.review.review_fixtures import load_review_fixture

_REPO_ROOT = Path(__file__).resolve().parents[4]

# Shared ``git diff`` config/flags mirroring
# lintro.ai.review.context.git_ops._git_diff_triple_snapshot so mocked argv
# matches the real invocation exactly.
_DIFF_SNAPSHOT_BASE_ARGV: list[str] = [
    "git",
    "-c",
    "diff.mnemonicPrefix=false",
    "-c",
    "diff.noprefix=false",
    "-c",
    "color.ui=false",
    "diff",
    "-M",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
]


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
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return the next queued response for ``args``."""
        _assert_review_subprocess_kwargs(kwargs=kwargs)
        normalized_args = [Path(args[0]).name, *args[1:]]
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
    """Register responses for the three ``git diff`` snapshot invocations.

    Mirrors ``_git_diff_triple_snapshot`` which now issues three separate git
    calls (unified diff, name-status, numstat) instead of one bash script.

    Args:
        dispatcher: The subprocess mock to register responses on.
        diff_ref: The diff target (for example ``base...head`` or ``HEAD``).
        unified: Stdout for the unified diff call.
        name_status: Stdout for the ``--name-status -z`` call.
        numstat: Stdout for the ``--numstat -z`` call.
    """
    ref_args = diff_ref.split(" ")
    dispatcher.queue([*_DIFF_SNAPSHOT_BASE_ARGV, *ref_args], stdout=unified)
    dispatcher.queue(
        [*_DIFF_SNAPSHOT_BASE_ARGV, "--name-status", "-z", *ref_args],
        stdout=name_status,
    )
    dispatcher.queue(
        [*_DIFF_SNAPSHOT_BASE_ARGV, "--numstat", "-z", *ref_args],
        stdout=numstat,
    )


@pytest.fixture(autouse=True)
def _rubric_only_review(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip the once-per-run question pass unless a test opts in (#2720).

    The suite's scripted providers answer a fixed number of calls, and the
    question pass is one more call per run. A test without the
    ``generated_questions`` marker therefore reviews with the rubric alone,
    exactly as ``ai.review_generated_questions = false`` does; tests of the
    pass itself carry the marker.

    Args:
        request: The requesting test, checked for the marker.
        monkeypatch: Pytest monkeypatch fixture.
    """
    if request.node.get_closest_marker("generated_questions"):
        return

    async def _rubric_only(**_kwargs: object) -> RunQuestions:
        return RunQuestions()

    monkeypatch.setattr(
        "lintro.ai.review.run_execution.run_question_pass",
        _rubric_only,
    )


@pytest.fixture(autouse=True)
def _unverified_review(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip the verification pass unless a test opts in (#2728).

    The suite's scripted providers answer a fixed number of calls, and the
    verification pass is one more call per round when anything is selected.
    A test without the ``verification`` marker therefore runs the round with
    ``review.verify: off`` exactly as configuration does; tests of the pass
    itself carry the marker.

    Args:
        request: The requesting test, checked for the marker.
        monkeypatch: Pytest monkeypatch fixture.
    """
    if request.node.get_closest_marker("verification"):
        return

    async def _unverified(**kwargs: object) -> VerificationPass:
        req = kwargs["request"]
        findings = tuple(req.findings)  # type: ignore[attr-defined]
        return VerificationPass(findings=findings, summary=VerificationSummary())

    monkeypatch.setattr(
        "lintro.ai.review.run_finalize.run_verification_pass",
        _unverified,
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
            checklist_items=3,
            token_usage={"prompt": 1000, "completion": 200, "total": 1200},
            cost_estimate_usd=0.05,
            base_ref="main",
            head_ref="feature",
            timestamp="2026-06-24T10:00:00+00:00",
        ),
        summary="Merge with fixes.",
        findings=(
            ReviewFinding(
                severity=Severity.P1,
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
                severity=Severity.P2,
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
