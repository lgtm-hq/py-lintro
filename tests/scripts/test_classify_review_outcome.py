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

from lintro.ai.review.enums.inline_post_failure_kind import InlinePostFailureKind
from lintro.ai.review.error_contract import (
    REVIEW_ERROR_EXIT_CODE,
    render_error_contract_json,
)
from lintro.ai.review.errors_taxonomy import ReviewErrorKind
from lintro.ai.review.github_render import format_inline_post_cause
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.output import (
    CONVERGED_ENVELOPE_KEY,
    CONVERGED_OUTCOME,
    INLINE_POST_FAILURE_KEY,
    finding_to_dict,
    render_convergence_outcome_json,
    render_inline_post_failure_json,
)

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
    assert_that(report.headline).contains("[cli]")
    assert_that(report.transport).is_equal_to("cli")


def _incomplete_envelope(*, stopped_reason: str = "cost cap") -> str:
    """Return a persist JSON envelope with incomplete coverage.

    Args:
        stopped_reason: Why the review stopped early.

    Returns:
        Captured-output text containing the coverage envelope.
    """
    return json.dumps(
        {
            "readiness_verdict": "incomplete",
            "coverage": {
                "reviewed": 2,
                "carried": 0,
                "awaiting": 5,
                "invalidated": 0,
                "eligible": 7,
                "covered_at_head": 2,
                "complete": False,
            },
            "stopped_reason": stopped_reason,
            "partial": True,
        },
    )


def test_incomplete_coverage_reddens_the_check(classifier: ModuleType) -> None:
    """A produced review with incomplete coverage must fail the check (#2154)."""
    report = classifier.classify(status=0, output=_incomplete_envelope())

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.INCOMPLETE)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.headline).contains("2/7 files covered at HEAD")


def test_sigterm_status_with_persist_envelope_is_incomplete(
    classifier: ModuleType,
) -> None:
    """``wait`` 143 after a SIGTERM persist must not read as unexpected (#2166)."""
    report = classifier.classify(
        status=classifier.SIGTERM_STATUS,
        output=_incomplete_envelope(stopped_reason="timeout (SIGTERM)"),
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.INCOMPLETE)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.headline).contains("2/7 files covered at HEAD")
    assert_that(report.headline).does_not_contain("unexpected status")
    assert_that(report.detail).contains("SIGTERM")


def test_error_status_with_persist_envelope_is_incomplete(
    classifier: ModuleType,
) -> None:
    """A persist envelope beats an error status so resume is not discarded."""
    report = classifier.classify(
        status=classifier.REVIEW_STATUS_ERROR,
        output=_incomplete_envelope(stopped_reason="timeout (SIGTERM)"),
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.INCOMPLETE)
    assert_that(report.headline).does_not_contain("nothing was reviewed")


def test_sigterm_status_with_complete_envelope_is_reviewed(
    classifier: ModuleType,
) -> None:
    """A finished envelope must stay REVIEWED when wait reports SIGTERM."""
    output = json.dumps(
        {
            "readiness_verdict": "ready",
            "coverage": {
                "reviewed": 3,
                "carried": 0,
                "awaiting": 0,
                "invalidated": 0,
                "eligible": 3,
                "covered_at_head": 3,
                "complete": True,
            },
        },
    )
    report = classifier.classify(status=classifier.SIGTERM_STATUS, output=output)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.headline).does_not_contain("unexpected status")
    assert_that(report.headline).contains("no P1 findings")


def test_sigterm_status_with_complete_p1_envelope_names_findings(
    classifier: ModuleType,
) -> None:
    """A finished P1 envelope must not be labelled clean after SIGTERM."""
    output = json.dumps(
        {
            "readiness_verdict": "ready",
            "coverage": {
                "reviewed": 3,
                "carried": 0,
                "awaiting": 0,
                "invalidated": 0,
                "eligible": 3,
                "covered_at_head": 3,
                "complete": True,
            },
            "findings": [{"severity": "P1", "title": "bug"}],
        },
    )
    report = classifier.classify(status=classifier.SIGTERM_STATUS, output=output)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.headline).contains("P1 findings posted")


def test_review_with_findings_still_passes(classifier: ModuleType) -> None:
    """Findings mean the review worked; they must not redden an advisory check.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(status=1, output="")

    assert_that(report.outcome.produced_review).is_true()
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.headline).contains("P1 findings")


def _inline_failure_log(*, kind: str, status: int) -> str:
    """Render the inline-post failure envelope as lintro logs it.

    Args:
        kind: Classified failure kind.
        status: HTTP status GitHub answered the review POST with.

    Returns:
        Captured-output text containing the envelope.
    """
    failure_kind = InlinePostFailureKind(kind)
    payload = {
        INLINE_POST_FAILURE_KEY: {
            "kind": failure_kind.value,
            "count": 94,
            "reason": format_inline_post_cause(kind=failure_kind, status=status),
            "status": status,
        },
    }
    return f"log line\ninline comments were not posted: {json.dumps(payload)}\n"


def test_rejected_inline_batch_is_reported_as_sticky_only(
    classifier: ModuleType,
) -> None:
    """A round GitHub throttled must not claim its findings went up inline.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=1,
        output=_inline_failure_log(kind="rate_limited", status=403),
    )

    assert_that(report.outcome.produced_review).is_true()
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.headline).contains("sticky comment only")
    assert_that(report.headline).contains("rate_limited")
    assert_that(report.headline).does_not_contain("P1 findings posted")
    assert_that(report.detail).contains("rate limit")


def test_clean_round_with_a_rejected_inline_batch_is_still_sticky_only(
    classifier: ModuleType,
) -> None:
    """The fallback is named even when no P1 finding was raised.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=0,
        output=_inline_failure_log(kind="line_mapping", status=422),
    )

    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.headline).contains("sticky comment only (line_mapping)")


def test_real_envelope_round_trips_from_lintro_to_the_classifier(
    classifier: ModuleType,
) -> None:
    """The envelope lintro renders is the one the classifier reads.

    Producer and consumer are wired through the real serializer rather than
    a hand-built log line, so a wrapped or renamed envelope on either side
    breaks this test instead of silently restoring "P1 findings posted".

    Args:
        classifier: The loaded classifier module.
    """
    finding = ReviewFinding(
        severity=Severity.P1,
        category="logic-bug",
        file="lintro/a.py",
        line=3,
        title="Off by one",
        description="The loop stops early.",
        cause="",
        fix="",
        confidence="high",
    )
    failure = InlinePostFailure(
        reason=format_inline_post_cause(
            kind=InlinePostFailureKind.RATE_LIMITED,
            status=429,
        ),
        findings=(finding,),
        kind=InlinePostFailureKind.RATE_LIMITED,
        status=429,
    )
    output = (
        "review log line\n"
        "Inline review comments were not posted; this round's findings "
        f"reached the sticky comment only: {render_inline_post_failure_json(failure=failure)}\n"
    )

    report = classifier.classify(status=1, output=output)

    assert_that(report.headline).contains("sticky comment only (rate_limited)")
    assert_that(report.headline).does_not_contain("P1 findings posted")
    assert_that(report.detail).contains("HTTP 429")


def test_inline_post_failure_key_matches_the_lintro_payload(
    classifier: ModuleType,
) -> None:
    """The classifier's copy of the envelope key cannot drift from lintro's.

    Args:
        classifier: The loaded classifier module.
    """
    assert_that(classifier.INLINE_POST_FAILURE_KEY).is_equal_to(
        INLINE_POST_FAILURE_KEY,
    )


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
    assert_that(report.detail).contains("CLAUDE_CODE_OAUTH_TOKEN")


def test_missing_credential_on_api_transport_names_the_api_key(
    classifier: ModuleType,
) -> None:
    """The api-transport no-credential guidance points at the API key secret.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=classifier.NO_CREDENTIAL_STATUS,
        output="",
        transport="api",
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.NO_CREDENTIAL)
    assert_that(report.headline).contains("[api]")
    assert_that(report.detail).contains("ANTHROPIC_API_KEY")
    assert_that(report.detail).does_not_contain("CLAUDE_CODE_OAUTH_TOKEN")


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
    # A red check with no reason attached is barely better than a green one, so
    # the raw output stands in when there is no envelope to read.
    assert_that(report.detail).contains("boom")


def test_never_invoked_status_reports_the_supplied_reason(
    classifier: ModuleType,
) -> None:
    """A wrapper-side abort must still explain itself, not just go red.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=classifier.NOT_INVOKED_STATUS,
        output="",
        reason="No PR number provided.",
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.BROKEN)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.headline).contains("never invoked")
    assert_that(report.detail).contains("No PR number provided.")


def test_never_invoked_falls_back_to_captured_output(
    classifier: ModuleType,
) -> None:
    """With no reason supplied, the captured output stands in.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=classifier.NOT_INVOKED_STATUS,
        output="uv: command not found",
    )

    assert_that(report.detail).contains("uv: command not found")


def test_annotation_percent_signs_are_escaped(
    classifier: ModuleType,
    capsys: object,
) -> None:
    """Workflow commands need `%` percent-encoded or the payload is mangled.

    Args:
        classifier: The loaded classifier module.
        capsys: Pytest capture fixture.
    """
    report = classifier.OutcomeReport(
        outcome=classifier.ReviewOutcome.BROKEN,
        headline="quota 100% consumed",
        detail="",
        exit_code=1,
    )
    classifier._emit(report=report)

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    annotation = next(
        line for line in captured.out.splitlines() if line.startswith("::error")
    )
    assert_that(annotation).contains("100%25 consumed")


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


# --- transport-aware taxonomy (#1923) ----------------------------------------


def test_headlines_name_the_transport(classifier: ModuleType) -> None:
    """Every outcome line names the transport so CI is self-describing.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=0,
        output="{}",
        transport="api",
    )

    assert_that(report.headline).starts_with("[api]")
    assert_that(report.transport).is_equal_to("api")


def test_api_auth_failed_is_labelled_key(classifier: ModuleType) -> None:
    """API transport auth failures use the auth_failed:key vocabulary.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=_envelope(kind="auth_failed", unavailable=True),
        transport="api",
    )

    assert_that(report.headline).contains("auth_failed:key")
    assert_that(report.headline).contains("[api]")


def test_cli_auth_failed_is_labelled_oauth_session(classifier: ModuleType) -> None:
    """CLI transport auth failures use the oauth_session vocabulary.

    Args:
        classifier: The loaded classifier module.
    """
    payload = {
        "error": {
            "kind": "auth_failed",
            "provider_unavailable": True,
            "message": "Not logged in · Please run /login",
        },
    }
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=json.dumps(payload),
        transport="cli",
    )

    assert_that(report.headline).contains("auth_failed:oauth_session")
    assert_that(report.outcome).is_equal_to(
        classifier.ReviewOutcome.PROVIDER_UNAVAILABLE,
    )


def test_oauth_prose_does_not_remap_a_concrete_kind(classifier: ModuleType) -> None:
    """OAuth wording in stderr must not hijack a non-auth envelope kind.

    A credits failure whose output happens to mention the OAuth session used
    to stay ``insufficient_credits`` — remapping it to
    ``auth_failed:oauth_session`` sends the operator to /login instead of
    billing.

    Args:
        classifier: The loaded classifier module.
    """
    payload = {
        "error": {
            "kind": "insufficient_credits",
            "provider_unavailable": True,
            "message": "OAuth session active but the credit balance is empty",
        },
    }
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=json.dumps(payload),
        transport="cli",
    )

    assert_that(report.headline).contains("insufficient_credits")
    assert_that(report.headline).does_not_contain("auth_failed")


def test_oauth_prose_classifies_a_thin_envelope(classifier: ModuleType) -> None:
    """Without an envelope kind, OAuth prose still labels the auth failure.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output="Not logged in · Please run /login",
        transport="cli",
    )

    assert_that(report.headline).contains("auth_failed:oauth_session")


def test_cli_timeout_is_turn_timeout(classifier: ModuleType) -> None:
    """CLI timeouts are labelled turn_timeout, not a generic timeout.

    Args:
        classifier: The loaded classifier module.
    """
    payload = {
        "error": {
            "kind": "timeout",
            "provider_unavailable": False,
            "message": "claude timed out after 900s",
        },
    }
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=json.dumps(payload),
        transport="cli",
    )

    assert_that(report.headline).contains("turn_timeout")
    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.BROKEN)


def test_cli_version_drift_is_detected(classifier: ModuleType) -> None:
    """CLI version drift is a broken outcome with a specific kind.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output="error: unknown option '--json-schema-name'",
        transport="cli",
    )

    assert_that(report.headline).contains("cli_version_drift")


def test_killed_externally_is_detected(classifier: ModuleType) -> None:
    """A runner kill must not be misread as a billing failure.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output="The operation was canceled: runner shutdown",
        transport="cli",
    )

    assert_that(report.headline).contains("killed_externally")
    assert_that(report.headline).does_not_contain("insufficient_credits")


def test_summary_names_transport(classifier: ModuleType) -> None:
    """The job summary header includes the transport.

    Args:
        classifier: The loaded classifier module.
    """
    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=_envelope(kind="insufficient_credits", unavailable=True),
        transport="api",
    )
    summary = classifier.render_summary(report=report)

    assert_that(summary).contains("AI Review (api)")
    assert_that(summary).contains("[api]")


def test_main_accepts_transport_flag(
    classifier: ModuleType,
    tmp_path: Path,
) -> None:
    """The CLI entry point forwards --transport into classification.

    Args:
        classifier: The loaded classifier module.
        tmp_path: Directory holding the captured-output file.
    """
    output_file = tmp_path / "review.log"
    output_file.write_text(
        _envelope(kind="auth_failed", unavailable=True),
        encoding="utf-8",
    )

    code = classifier.main(
        argv=[
            "--status",
            str(REVIEW_ERROR_EXIT_CODE),
            "--output-file",
            str(output_file),
            "--transport",
            "api",
        ],
    )

    assert_that(code).is_equal_to(1)


def test_main_transport_flag_reaches_summary_and_labels(
    classifier: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--transport api drives the summary header and the api vocabulary.

    Args:
        classifier: The loaded classifier module.
        tmp_path: Directory holding the captured-output file.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Captured stdout/stderr.
    """
    output_file = tmp_path / "review.log"
    output_file.write_text(
        _envelope(kind="auth_failed", unavailable=True),
        encoding="utf-8",
    )
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    code = classifier.main(
        argv=[
            "--status",
            str(REVIEW_ERROR_EXIT_CODE),
            "--output-file",
            str(output_file),
            "--transport",
            "api",
        ],
    )

    assert_that(code).is_equal_to(1)
    summary = summary_file.read_text(encoding="utf-8")
    assert_that(summary).contains("AI Review (api)")
    assert_that(summary).contains("auth_failed:key")
    assert_that(capsys.readouterr().out).contains("[api]")


# --- converged rounds (#2099) ------------------------------------------------


def _converged_output(*, round_number: int = 3) -> str:
    """Render what ``lintro review`` prints when the stop rule fires.

    Built by lintro's own renderer rather than hand-written JSON, so the
    classifier is tested against the envelope actually emitted.

    Args:
        round_number: Round the stop rule skipped.

    Returns:
        Captured-output text containing the envelope.
    """
    decision = ConvergenceDecision(
        converged=True,
        round_number=round_number,
        score=0.5,
        threshold=3.0,
        stable_rounds=2,
        trajectory=(1.0, 0.5),
    )
    return f"some log line\n{render_convergence_outcome_json(decision=decision)}"


def test_converged_envelope_is_its_own_green_outcome(classifier: ModuleType) -> None:
    """A deliberately skipped round is neither a review nor a failure.

    Args:
        classifier: Loaded classifier module.
    """
    report = classifier.classify(status=0, output=_converged_output())

    assert_that(str(report.outcome)).is_equal_to("converged")
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.outcome.produced_review).is_false()
    assert_that(report.outcome.review_unavailable).is_false()


def test_converged_headline_names_the_skipped_round(classifier: ModuleType) -> None:
    """CI states which round was skipped and on what evidence.

    Args:
        classifier: Loaded classifier module.
    """
    report = classifier.classify(status=0, output=_converged_output(round_number=7))

    assert_that(report.headline).contains("converged")
    assert_that(report.headline).contains("round 7")
    assert_that(report.headline).contains("2 stable rounds")
    assert_that(report.detail).contains("score 0.50 < threshold 3.00")


def test_converged_summary_does_not_tell_reviewers_to_fall_back(
    classifier: ModuleType,
) -> None:
    """The un-reviewed advice belongs to failures, not to a chosen stop.

    Args:
        classifier: Loaded classifier module.
    """
    report = classifier.classify(status=0, output=_converged_output())

    summary = classifier.render_summary(report=report)

    assert_that(summary).contains("No provider call was made")
    assert_that(summary).does_not_contain("fall back to CodeRabbit")


def test_converged_envelope_key_matches_the_producer(classifier: ModuleType) -> None:
    """The classifier keys on the exact key and discriminator lintro writes.

    Args:
        classifier: Loaded classifier module.
    """
    assert_that(classifier.CONVERGED_ENVELOPE_KEY).is_equal_to(CONVERGED_ENVELOPE_KEY)
    assert_that(classifier.CONVERGED_OUTCOME).is_equal_to(CONVERGED_OUTCOME)


def test_a_nested_converged_object_does_not_classify_a_real_review_as_a_skip(
    classifier: ModuleType,
) -> None:
    """A finding that merely mentions ``converged`` is not the stop envelope.

    The shared JSON scan tries every ``{``, so nested objects are yielded too.
    Without the top-level ``outcome`` discriminator, a reviewed round carrying
    a nested ``converged`` mapping would be reported as a skipped one — a
    review that really ran would vanish from CI (#2099 review).

    Args:
        classifier: Loaded classifier module.
    """
    output = json.dumps(
        {
            "readiness_verdict": "blocked",
            "findings": [
                {"title": "x", "meta": {"converged": {"round": 9, "open_p1": 0}}},
            ],
        },
    )

    report = classifier.classify(status=1, output=output)

    assert_that(str(report.outcome)).is_equal_to("reviewed")
    assert_that(report.headline).contains("P1 findings")


def test_the_converged_envelope_is_still_found_after_leading_log_lines(
    classifier: ModuleType,
) -> None:
    """The discriminator did not cost the parser its real envelope.

    Args:
        classifier: Loaded classifier module.
    """
    output = f"INFO starting review\n{{ not json\n{_converged_output()}"

    report = classifier.classify(status=0, output=output)

    assert_that(str(report.outcome)).is_equal_to("converged")


def test_a_normal_review_is_still_classified_as_reviewed(
    classifier: ModuleType,
) -> None:
    """The converged branch does not swallow ordinary review output.

    Args:
        classifier: Loaded classifier module.
    """
    report = classifier.classify(
        status=0,
        output=json.dumps({"readiness_verdict": "ready", "findings": []}),
    )

    assert_that(str(report.outcome)).is_equal_to("reviewed")
    assert_that(report.exit_code).is_equal_to(0)


def _converged_envelope(*, open_p1: int = 0) -> str:
    """Render the envelope exactly as ``lintro review`` emits it.

    Args:
        open_p1: Open P1 findings the last real round left in force.

    Returns:
        The JSON text the producer writes on a converged skip.
    """
    from lintro.ai.review.models.convergence_decision import ConvergenceDecision
    from lintro.ai.review.output import render_convergence_outcome_json

    decision = ConvergenceDecision(
        converged=True,
        round_number=3,
        score=0.5,
        threshold=3.0,
        stable_rounds=2,
        trajectory=(1.0, 0.5),
    )
    return render_convergence_outcome_json(decision=decision, open_p1=open_p1)


def test_converged_report_annotates_as_notice_and_main_exits_zero(
    classifier: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A skipped-because-converged round is green end to end.

    Args:
        classifier: Loaded classifier module.
        tmp_path: Directory holding the captured-output file.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Captured stdout/stderr.
    """
    output_file = tmp_path / "review.log"
    output_file.write_text(_converged_envelope(), encoding="utf-8")
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    code = classifier.main(
        argv=["--status", "0", "--output-file", str(output_file), "--transport", "cli"],
    )
    out = capsys.readouterr().out

    assert_that(code).is_equal_to(0)
    assert_that(out).contains("::notice")
    assert_that(out).does_not_contain("::error")
    summary = summary_file.read_text(encoding="utf-8")
    assert_that(summary).contains("🔁")
    assert_that(summary).does_not_contain("CodeRabbit")


@pytest.mark.parametrize(
    "bad",
    [None, True, "P1", 1.5, -1, [], {}],
    ids=["missing", "boolean", "string", "fraction", "negative", "list", "dict"],
)
def test_an_unreadable_open_p1_fails_the_skip_gate_closed(
    classifier: ModuleType,
    bad: object,
) -> None:
    """A count that cannot be read must not become a green check.

    ``open_p1`` is the entire readiness gate for a skipped round. Degrading
    an unreadable value to zero would turn a malformed envelope into a clean
    pass — the silent success this module exists to prevent.

    Args:
        classifier: Loaded classifier module.
        bad: Unusable ``open_p1`` value under test.
    """
    payload = json.loads(_converged_envelope())
    if bad is None:
        del payload[CONVERGED_ENVELOPE_KEY]["open_p1"]
    else:
        payload[CONVERGED_ENVELOPE_KEY]["open_p1"] = bad

    report = classifier.classify(
        status=0,
        output=json.dumps(payload),
        transport="cli",
    )

    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.outcome.review_unavailable).is_true()
    assert_that(report.headline).contains("unreadable")


@pytest.mark.parametrize(
    ("raw", "expected_exit"),
    [("2", 1), (2.0, 1), ("0", 0), (0.0, 0)],
    ids=["numeric string blocks", "whole float blocks", "string zero", "float zero"],
)
def test_a_numeric_open_p1_is_read_as_a_count(
    classifier: ModuleType,
    raw: object,
    expected_exit: int,
) -> None:
    """A count a JSON producer spelled as a string or float is still a count.

    Args:
        classifier: Loaded classifier module.
        raw: ``open_p1`` value under test.
        expected_exit: Exit code the count should produce.
    """
    payload = json.loads(_converged_envelope())
    payload[CONVERGED_ENVELOPE_KEY]["open_p1"] = raw

    report = classifier.classify(
        status=0,
        output=json.dumps(payload),
        transport="cli",
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONVERGED)
    assert_that(report.exit_code).is_equal_to(expected_exit)


def test_a_hard_failure_after_the_skip_envelope_is_not_hidden_by_it(
    classifier: ModuleType,
) -> None:
    """An error following a converged envelope wins over the skip.

    The stop rule exits 0 or 1 and never 2, so status 2 alongside a converged
    envelope means something broke after the envelope was printed. Reporting
    the skip would bury that failure behind a green-looking outcome.

    Args:
        classifier: Loaded classifier module.
    """
    from lintro.ai.exceptions import AIProviderError

    rendered = render_error_contract_json(
        provider="anthropic",
        error=AIProviderError(
            "Anthropic API error: Error code: 400 - Your credit balance is too low",
        ),
    )
    output = f"{_converged_envelope()}\n{rendered}\n"

    report = classifier.classify(
        status=REVIEW_ERROR_EXIT_CODE,
        output=output,
        transport="cli",
    )

    assert_that(report.outcome).is_not_equal_to(classifier.ReviewOutcome.CONVERGED)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.outcome.review_unavailable).is_true()


def test_a_blocking_skip_reddens_main_end_to_end(
    classifier: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The open-P1 skip is pinned through main()/_emit, not just classify().

    Args:
        classifier: Loaded classifier module.
        tmp_path: Directory holding the captured-output file.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Captured stdout/stderr.
    """
    output_file = tmp_path / "review.log"
    output_file.write_text(_converged_envelope(open_p1=2), encoding="utf-8")
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    code = classifier.main(
        argv=["--status", "1", "--output-file", str(output_file), "--transport", "cli"],
    )
    out = capsys.readouterr().out

    assert_that(code).is_equal_to(1)
    assert_that(out).contains("2 open P1 still blocking")
    summary = summary_file.read_text(encoding="utf-8")
    assert_that(summary).contains("2 open P1 still blocking")
    # A skip is a decision, not an outage: never the fall-back-to-CodeRabbit copy.
    assert_that(summary).does_not_contain("CodeRabbit")


def test_a_p1_question_does_not_redden_the_recovered_review(
    classifier: ModuleType,
) -> None:
    """The CI P1 gate excludes questions exactly as the CLI one does.

    Otherwise the CLI would exit 0 for a round of P1 questions while the
    check summary announced P1 findings on the same output.

    Args:
        classifier: Loaded classifier module.
    """
    from lintro.ai.review.enums.finding_kind import FindingKind

    payload = {
        "readiness_verdict": "ready",
        "coverage": {"complete": True, "covered_at_head": 1, "eligible": 1},
        "findings": [
            finding_to_dict(
                finding=ReviewFinding(
                    severity=Severity.P1,
                    category="clarification",
                    file="a.py",
                    line=1,
                    title="Why is this here?",
                    description="d",
                    cause="c",
                    fix="f",
                    confidence="high",
                    kind=FindingKind.QUESTION,
                ),
            ),
        ],
    }

    report = classifier.classify(
        status=143,
        output=json.dumps(payload),
        transport="cli",
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.headline).contains("no P1 findings")


def test_converged_skip_with_an_open_p1_keeps_the_check_red(
    classifier: ModuleType,
) -> None:
    """The classifier mirrors the CLI: an open P1 left in force still exits 1.

    Args:
        classifier: Loaded classifier module.
    """
    report = classifier.classify(
        status=1,
        output=_converged_envelope(open_p1=2),
        transport="cli",
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONVERGED)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.headline).contains("2 open P1 still blocking")
    assert_that(report.outcome.review_unavailable).is_false()
