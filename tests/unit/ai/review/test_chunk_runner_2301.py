"""Tests for the chunk-runner seams extracted in #2301.

Two properties the rest of the suite exercises only indirectly: the mid-run
coverage checkpoint is best-effort (a broken state directory must never fail the
review it protects), and the built-in review passes share exactly one
``call_ai`` seam so a test that stubs the provider sees every call.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.review import (
    adversarial_pass,
    checklist_pass,
    provider_call,
    response_pipeline,
)
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.incremental_coverage import checkpoint_writer
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.resume import plan_resume
from lintro.ai.review.sensitivity import resolve_sensitivity_policy

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintro.ai.review.resume import ResumePlan

#: The modules whose provider calls must all resolve through one seam.
_BUILT_IN_PASS_MODULES = (response_pipeline, checklist_pass, adversarial_pass)


def _context() -> ReviewContext:
    """Build a one-file review context.

    Returns:
        A context with a single modified Python file.
    """
    return ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(
                path="src/app.py",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=(
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1 +1,2 @@\n value = 1\n+value = 2\n"
        ),
    )


def _partial() -> ChunkReviewPartial:
    """Build an empty chunk partial for the fixture file.

    Returns:
        A partial that reviewed the fixture file and reported nothing.
    """
    return ChunkReviewPartial(
        summary="",
        checklist=(),
        findings=(),
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
        files=("src/app.py",),
    )


def _resume(context: ReviewContext) -> ResumePlan:
    """Build a resume plan covering the fixture context.

    Args:
        context: The review context to plan against.

    Returns:
        A full-review resume plan.
    """
    return plan_resume(
        context=context,
        prior=None,
        extra_skips=[],
        groups=(("src/app.py",),),
        force_full=True,
    )


def _writer(
    context: ReviewContext,
) -> Callable[[list[ChunkReviewPartial]], None]:
    """Build a checkpoint callback for the fixture context.

    Args:
        context: The review context to checkpoint against.

    Returns:
        The callback the chunk fan-out would invoke.
    """
    return checkpoint_writer(
        resume=_resume(context),
        context=context,
        prior_state=None,
        force_full=True,
        policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
    )


def test_checkpoint_writer_numbers_parts_monotonically(tmp_path: Path) -> None:
    """Each completed chunk writes the next numbered coverage part.

    Args:
        tmp_path: Pytest temporary directory used as the state directory.
    """
    context = _context()
    checkpoint = _writer(context)
    written: list[int] = []

    with (
        patch.dict(
            "os.environ",
            {"LINTRO_REVIEW_STATE_DIR": str(tmp_path)},
        ),
        patch(
            "lintro.ai.review.incremental_coverage.write_state_part",
            side_effect=lambda **kwargs: written.append(kwargs["sequence"]),
        ),
    ):
        checkpoint([_partial()])
        checkpoint([_partial()])

    assert_that(written).is_equal_to([1, 2])


def test_checkpoint_writer_survives_a_failed_part(tmp_path: Path) -> None:
    """A part that cannot be written is logged, not raised, and not counted.

    The checkpoint exists so a SIGTERM does not lose completed work; a broken
    state directory must never fail the review it is protecting, and the
    sequence must not advance past a part that was never written.

    Args:
        tmp_path: Pytest temporary directory used as the state directory.
    """
    context = _context()
    checkpoint = _writer(context)
    attempts: list[int] = []

    def _write(**kwargs: object) -> None:
        """Fail the first part and record every attempted sequence number.

        Args:
            **kwargs: Keyword arguments ``write_state_part`` was called with.

        Raises:
            OSError: On the first attempt only.
        """
        sequence = int(str(kwargs["sequence"]))
        attempts.append(sequence)
        if len(attempts) == 1:
            msg = "state directory is read-only"
            raise OSError(msg)

    with (
        patch.dict(
            "os.environ",
            {"LINTRO_REVIEW_STATE_DIR": str(tmp_path)},
        ),
        patch(
            "lintro.ai.review.incremental_coverage.write_state_part",
            side_effect=_write,
        ),
    ):
        checkpoint([_partial()])
        checkpoint([_partial()])

    assert_that(attempts).is_equal_to([1, 1])


def test_checkpoint_writer_is_inert_without_a_state_directory(tmp_path: Path) -> None:
    """A local review writes nothing: the checkpoint is a CI-only artifact.

    Args:
        tmp_path: Pytest temporary directory, deliberately not configured.
    """
    context = _context()
    checkpoint = _writer(context)
    calls: list[object] = []

    with (
        patch.dict("os.environ", {"LINTRO_REVIEW_STATE_DIR": ""}),
        patch(
            "lintro.ai.review.incremental_coverage.write_state_part",
            side_effect=calls.append,
        ),
    ):
        checkpoint([_partial()])

    assert_that(calls).is_empty()
    assert_that(list(tmp_path.iterdir())).is_empty()


def test_built_in_passes_do_not_bind_call_ai_themselves() -> None:
    """No pass module rebinds ``call_ai``, so one patch target covers them all.

    ``from lintro.ai.invoke import call_ai`` in any of these modules would
    reopen the per-module seam #2301 closed: a depth >= 2 test would then have
    to patch three targets again, and forgetting one would let a real provider
    call escape.
    """
    rebinding = sorted(
        module.__name__
        for module in _BUILT_IN_PASS_MODULES
        if "call_ai" in vars(module)
    )

    assert_that(rebinding).is_empty()


def test_the_seam_module_exposes_the_shared_call_ai() -> None:
    """``provider_call.call_ai`` is the invoke-layer entry point, unwrapped."""
    from lintro.ai.invoke import call_ai

    assert_that(provider_call.call_ai).is_same_as(call_ai)
