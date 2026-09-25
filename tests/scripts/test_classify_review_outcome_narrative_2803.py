"""Narrative degradations warn without reddening the AI Review check (#2803)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

from lintro.ai.review.coverage_degradation import GENERATED_QUESTIONS_FAILED_NOTE
from lintro.ai.review.enums.coverage_degradation_reason import (
    NARRATIVE_DEGRADATION_REASONS,
    CoverageDegradationReason,
)

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "classify_review_outcome.py"
)


@pytest.fixture
def classifier(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the classifier script as a module.

    Args:
        monkeypatch: Registers the module for the test only, so no entry
            outlives it.

    Returns:
        The classifier module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location("classify_review_outcome", SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "classify_review_outcome", module)
    spec.loader.exec_module(module)
    return module


def _envelope(*reasons: str, complete: bool) -> str:
    """Return a finished review envelope recording ``reasons``.

    Args:
        *reasons: Coverage-degradation reasons the run recorded.
        complete: Value of ``findings_coverage_complete``.

    Returns:
        Captured-output text containing the review JSON envelope.
    """
    return json.dumps(
        {
            "readiness_verdict": "ready",
            "findings": [],
            "findings_coverage_complete": complete,
            "coverage_degradations": [
                {"reason": reason, "chunk_index": -1} for reason in reasons
            ],
        },
    )


def test_the_narrative_reason_set_matches_lintro(classifier: ModuleType) -> None:
    """The script's mirror of the narrative set cannot drift from lintro's.

    Args:
        classifier: The loaded classifier module.
    """
    assert_that(set(classifier.NARRATIVE_DEGRADATION_REASONS)).is_equal_to(
        {str(reason) for reason in NARRATIVE_DEGRADATION_REASONS},
    )
    assert_that(classifier.GENERATED_QUESTIONS_FAILED_REASON).is_equal_to(
        str(CoverageDegradationReason.GENERATED_QUESTIONS_FAILED),
    )


def test_the_question_pass_warning_matches_the_sticky_note(
    classifier: ModuleType,
) -> None:
    """The ``::warning::`` and the sticky note are the same sentence.

    Args:
        classifier: The loaded classifier module.
    """
    assert_that(classifier.GENERATED_QUESTIONS_FAILED_NOTE).is_equal_to(
        GENERATED_QUESTIONS_FAILED_NOTE,
    )


def test_a_failed_question_pass_alone_passes_with_a_warning(
    classifier: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 0, one ``::warning::`` with the note, and the note in the summary.

    Args:
        classifier: The loaded classifier module.
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    output = tmp_path / "review.log"
    output.write_text(_envelope("generated_questions_failed", complete=True))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    code = classifier.main(argv=["--status", "0", "--output-file", str(output)])
    printed = capsys.readouterr().out

    assert_that(code).is_equal_to(0)
    assert_that(printed).contains(
        f"::warning title=AI Review (cli)::{GENERATED_QUESTIONS_FAILED_NOTE}",
    )
    assert_that(printed).contains("::notice title=AI Review (cli)::")
    assert_that(summary.read_text()).contains(f"> ⚠️ {GENERATED_QUESTIONS_FAILED_NOTE}")


def test_other_narrative_reasons_are_named_in_one_warning(
    classifier: ModuleType,
) -> None:
    """Synthesis and verification failures are warned about, not failed on.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=0,
        output=_envelope("synthesis_failed", "verification_failed", complete=True),
    )

    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.notes).is_equal_to(
        (
            "Recorded without failing this check: synthesis_failed, "
            "verification_failed.",
        ),
    )


def test_a_per_file_reason_still_fails_and_keeps_the_warning(
    classifier: ModuleType,
) -> None:
    """A per-file reason reddens as before; the narrative note rides along.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=0,
        output=_envelope(
            "adversarial_sweep_failed",
            "generated_questions_failed",
            complete=False,
        ),
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.DEGRADED)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.notes).is_equal_to((GENERATED_QUESTIONS_FAILED_NOTE,))


def test_a_clean_review_carries_no_notes(classifier: ModuleType) -> None:
    """A round with no degradation renders exactly as before.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(status=0, output=_envelope(complete=True))

    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.notes).is_empty()


def test_a_hard_failure_gets_no_notes(classifier: ModuleType) -> None:
    """At status 2 the failure is the news; no warning is attached.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=classifier.REVIEW_STATUS_ERROR,
        output=_envelope("generated_questions_failed", complete=True),
    )

    assert_that(report.notes).is_empty()
