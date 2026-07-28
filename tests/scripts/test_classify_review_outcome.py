"""Tests for the AI review outcome classifier (#1826).

The defect being guarded against is narrow and specific: the dogfood review check
reported ``success`` on every pull request while producing no review at all. The
classifier is the single place that decides "reviewed" from "did not review", so
these tests pin the exit-code contract — and, above all, that *no* provider
failure path can return 0.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

from lintro.ai.review.error_contract import (
    REVIEW_ERROR_EXIT_CODE,
    render_error_contract_json,
)
from lintro.ai.review.errors_taxonomy import ReviewErrorKind

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "classify_review_outcome.py"


def _load() -> ModuleType:
    """Load the classifier as an importable module.

    Returns:
        The loaded module exposing its public helpers.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location("classify_review_outcome", SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_review_outcome"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def classifier() -> ModuleType:
    """Return the loaded classifier module.

    Returns:
        The classifier module.
    """
    return _load()


def _envelope(*, kind: str, unavailable: bool) -> str:
    """Render a review error envelope as the review command would print it.

    Args:
        kind: Canonical error kind value.
        unavailable: Whether the provider served nothing.

    Returns:
        Captured-output text containing the JSON envelope.
    """
    payload = {
        "error": {
            "kind": kind,
            "provider": "anthropic",
            "status": 400,
            "retryable": False,
            "provider_unavailable": unavailable,
            "message": "Your credit balance is too low",
        },
    }
    return f"some log line\n{json.dumps(payload, indent=2)}\ntrailing log\n"


# --- exit-code contract ------------------------------------------------------


def test_clean_review_passes(classifier: ModuleType) -> None:
    """A review that ran and found nothing is the only silent success.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(status=0, output="{}")

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.headline).contains("no P1 findings")


def test_review_with_findings_still_passes(classifier: ModuleType) -> None:
    """Findings mean the review worked; they must not redden an advisory check.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(status=1, output="")

    assert_that(report.outcome.produced_review).is_true()
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.headline).contains("P1 findings")


def test_depleted_balance_never_reports_success(classifier: ModuleType) -> None:
    """The exact #1826 condition must fail the check, loudly.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=_envelope(kind="insufficient_credits", unavailable=True),
    )

    assert_that(report.outcome).is_equal_to(
        classifier.ReviewOutcome.PROVIDER_UNAVAILABLE,
    )
    assert_that(report.outcome.produced_review).is_false()
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.headline).contains("insufficient_credits")
    assert_that(report.detail).contains("credit balance")


def test_missing_credential_never_reports_success(classifier: ModuleType) -> None:
    """A review that was never attempted is still a review that did not happen.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=classifier.NO_CREDENTIAL_STATUS,
        output="",
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.NO_CREDENTIAL)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.detail).contains("ANTHROPIC_API_KEY")


def test_lintro_side_failure_is_not_blamed_on_the_provider(
    classifier: ModuleType,
) -> None:
    """A malformed response is lintro's problem and must be labelled as such.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=_envelope(kind="invalid_response", unavailable=False),
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.BROKEN)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.headline).contains("invalid_response")


def test_unexpected_exit_status_is_broken_not_unavailable(
    classifier: ModuleType,
) -> None:
    """An undefined status means the wrapper broke, not that the account did.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(status=127, output="command not found")

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.BROKEN)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.headline).contains("127")


def test_error_status_without_an_envelope_still_fails(
    classifier: ModuleType,
) -> None:
    """Unparseable output must not be able to smuggle through as a pass.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(status=REVIEW_ERROR_EXIT_CODE, output="boom")

    assert_that(report.outcome.produced_review).is_false()
    assert_that(report.exit_code).is_equal_to(1)


# --- envelope parsing --------------------------------------------------------


def test_parses_the_real_review_error_envelope(classifier: ModuleType) -> None:
    """The classifier reads lintro's actual rendering, not a hand-built stub.

    Args:
        classifier: The loaded classifier module.
    """
    from lintro.ai.exceptions import AIProviderError

    rendered = render_error_contract_json(
        provider="anthropic",
        error=AIProviderError(
            "Anthropic API error: Error code: 400 - Your credit balance is too low",
        ),
    )
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=f"log noise\n{rendered}\n",
    )

    assert_that(report.outcome).is_equal_to(
        classifier.ReviewOutcome.PROVIDER_UNAVAILABLE,
    )
    assert_that(report.headline).contains(
        ReviewErrorKind.INSUFFICIENT_CREDITS.value,
    )


def test_compact_envelope_is_also_parsed(classifier: ModuleType) -> None:
    """A non-indented envelope is located too, so formatting is not load-bearing.

    Args:
        classifier: The loaded classifier module.
    """
    payload = json.dumps(
        {
            "error": {
                "kind": "auth_failed",
                "provider_unavailable": True,
                "message": "401",
            },
        },
    )
    report = classifier.classify(status=REVIEW_ERROR_EXIT_CODE, output=payload)

    assert_that(report.outcome).is_equal_to(
        classifier.ReviewOutcome.PROVIDER_UNAVAILABLE,
    )


# --- surfaced copy -----------------------------------------------------------


def test_summary_tells_the_reader_the_diff_was_not_reviewed(
    classifier: ModuleType,
) -> None:
    """The job summary must state the consequence, not just the error.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=_envelope(kind="insufficient_credits", unavailable=True),
    )
    summary = classifier.render_summary(report=report)

    assert_that(summary).contains("AI Review")
    assert_that(summary).contains("no AI review was produced")
    assert_that(summary).contains("CodeRabbit/Greptile")


def test_annotation_is_single_line(classifier: ModuleType, capsys: object) -> None:
    """Workflow commands cannot span lines, so the payload must be flattened.

    Args:
        classifier: The loaded classifier module.
        capsys: Pytest capture fixture.
    """
    report = classifier.OutcomeReport(
        outcome=classifier.ReviewOutcome.BROKEN,
        headline="review could not complete",
        detail="line one\nline two",
        exit_code=1,
    )
    classifier._emit(report=report)

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    annotation = [
        line for line in captured.out.splitlines() if line.startswith("::error")
    ]
    assert_that(annotation).is_length(1)
    assert_that(annotation[0]).does_not_contain("line one\nline two")
    assert_that(annotation[0]).contains("line one line two")


def test_main_returns_the_report_exit_code(
    classifier: ModuleType,
    tmp_path: Path,
) -> None:
    """The CLI entry point propagates the verdict as its exit code.

    Args:
        classifier: The loaded classifier module.
        tmp_path: Directory holding the captured-output file.
    """
    output_file = tmp_path / "review.log"
    output_file.write_text(
        _envelope(kind="insufficient_credits", unavailable=True),
        encoding="utf-8",
    )

    code = classifier.main(
        argv=[
            "--status",
            str(REVIEW_ERROR_EXIT_CODE),
            "--output-file",
            str(output_file),
        ],
    )

    assert_that(code).is_equal_to(1)


def test_main_tolerates_a_missing_output_file(classifier: ModuleType) -> None:
    """A vanished capture file must not crash the classifier into a green pass.

    Args:
        classifier: The loaded classifier module.
    """
    code = classifier.main(
        argv=["--status", str(REVIEW_ERROR_EXIT_CODE), "--output-file", "/nope/x.log"],
    )

    assert_that(code).is_equal_to(1)
