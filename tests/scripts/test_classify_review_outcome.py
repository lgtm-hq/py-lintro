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
    assert_that(report.headline).contains("[cli]")
    assert_that(report.transport).is_equal_to("cli")


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
