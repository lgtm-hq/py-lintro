"""Wiring tests: an already-reviewed head short-circuits the command (#2338).

Modelled on the convergence wiring tests (#2099): the provider and the
orchestrator are counted, not stubbed silently, because "no provider call was
made" is the whole point of the guard.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.review import already_reviewed as guard_module
from lintro.ai.review import preparation as preparation_module
from lintro.ai.review import reuse_gate as reuse_gate_module
from lintro.ai.review.already_reviewed import AlreadyReviewed
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.cli_utils.commands import review as review_module
from lintro.cli_utils.commands.review import review_command
from lintro.config.review_config import ReviewConvergenceConfig

_HEAD = "sha9"
_DECISION = AlreadyReviewed(head_sha=_HEAD, round=2, files_covered=3, open_findings=1)


class _RoundRanError(Exception):
    """Raised by the ``run_review`` stub to mark that a round really started."""


@pytest.fixture
def review_calls() -> dict[str, int]:
    """Return a counter recording provider and orchestrator invocations.

    Returns:
        Mapping of collaborator name to call count.
    """
    return {"get_provider": 0, "run_review": 0}


@pytest.fixture
def patched_review(
    monkeypatch: pytest.MonkeyPatch,
    review_calls: dict[str, int],
) -> MagicMock:
    """Stub the review command up to the provider call.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        review_calls: Counter shared with the tests.

    Returns:
        The mutable project config the command will read.
    """
    from lintro.ai.config import AIConfig

    config = MagicMock()
    config.ai = {"enabled": True, "review": True, "provider": "anthropic"}
    config.review.depth = 1
    config.review.strictness = ReviewStrictness.BALANCED
    config.review.sensitivity = {}
    config.review.checklist_display = "off"
    config.review.force_semantic_chunking = False
    config.review.custom_agents = CustomAgentMode.DISABLED
    config.review.convergence = ReviewConvergenceConfig()

    def _count_provider(*_args: object, **_kwargs: object) -> MagicMock:
        review_calls["get_provider"] += 1
        return MagicMock(name="anthropic")

    def _count_run_review(*_args: object, **_kwargs: object) -> MagicMock:
        review_calls["run_review"] += 1
        raise _RoundRanError

    monkeypatch.setattr(review_module, "require_ai", lambda: None)
    monkeypatch.setattr(review_module, "get_config", lambda: config)
    monkeypatch.setattr(
        preparation_module,
        "collect_review_context",
        lambda **_: MagicMock(changed_files=[], head_ref=_HEAD),
    )
    monkeypatch.setattr(preparation_module, "classify_changed_files", lambda _: [])
    monkeypatch.setattr(preparation_module, "get_all_checklist_items", lambda **_: [])
    monkeypatch.setattr(preparation_module, "select_checklist_items", lambda **_: [])
    monkeypatch.setattr(
        preparation_module,
        "format_checklist_for_prompt",
        lambda **_: ("", {}),
    )
    monkeypatch.setattr(review_module, "build_prompt_question_map", lambda **_: {})
    monkeypatch.setattr(
        review_module,
        "resolve_checklist_display",
        lambda **_: ChecklistDisplay.OFF,
    )
    monkeypatch.setattr(
        review_module,
        "resolve_effective_ai_config",
        lambda _mapping, **_kwargs: AIConfig.resolve_from_mapping(
            {"enabled": True, "review": True, "provider": "anthropic"},
        ),
    )
    monkeypatch.setattr(
        preparation_module,
        "resolve_sensitivity_policy",
        lambda **_: MagicMock(),
    )
    monkeypatch.setattr(review_module, "get_provider", _count_provider)
    monkeypatch.setattr(preparation_module, "run_review", _count_run_review)
    monkeypatch.setattr(
        reuse_gate_module,
        "reusable_rounds_before_provider",
        lambda **_: None,
    )
    monkeypatch.delenv(guard_module.FORCE_ENV, raising=False)
    return config


def _with_prior_state(*, monkeypatch: pytest.MonkeyPatch, state: ReviewState) -> None:
    """Make the command load a fixed prior state.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        state: State to return from the loader.
    """
    monkeypatch.setattr(review_module, "load_prior_review_state", lambda **_: state)


def _decide(
    *,
    monkeypatch: pytest.MonkeyPatch,
    decision: AlreadyReviewed | None,
) -> dict[str, Any]:
    """Make the guard return *decision* and record what it was asked.

    The guard's own conditions are pinned in ``test_already_reviewed_2338``;
    here the seam between the guard and the command is what is under test.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        decision: What the guard answers.

    Returns:
        The keyword arguments the guard received, filled on the call.
    """
    seen: dict[str, Any] = {}

    def _evaluate(**kwargs: Any) -> AlreadyReviewed | None:
        seen.update(kwargs)
        return decision

    monkeypatch.setattr(review_module, "evaluate_already_reviewed", _evaluate)
    return seen


def _state(*findings: FindingRecord) -> ReviewState:
    """Return a state with one finished run at the head and *findings*.

    Args:
        *findings: Tracked finding records.

    Returns:
        The state.
    """
    return ReviewState(
        runs=(RunRecord(identity=RunIdentity(round=2, sha=_HEAD)),),
        findings=findings,
    )


def _open_p1() -> FindingRecord:
    """Return an open P1 record.

    Returns:
        The record.
    """
    return FindingRecord(
        fingerprint="fp-p1",
        severity=Severity.P1,
        file="a.py",
        line=1,
        title="blocker",
        status=FindingStatus.OPEN,
        inline_comment_id=11,
        confidence="high",
    )


def _envelope(*, output: str) -> dict[str, Any]:
    """Parse the JSON envelope out of captured CLI output.

    Args:
        output: Combined CLI output; lintro may log ahead of the payload.

    Returns:
        The decoded envelope.
    """
    decoded: dict[str, Any] = json.loads(output[output.index("{") :])
    return decoded


@pytest.mark.usefixtures("patched_review")
def test_a_covered_head_skips_the_round_without_calling_the_provider(
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No provider, no orchestrator, exit 0, the decision on the terminal."""
    _with_prior_state(monkeypatch=monkeypatch, state=_state())
    seen = _decide(monkeypatch=monkeypatch, decision=_DECISION)

    result = CliRunner().invoke(review_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(review_calls["get_provider"]).is_equal_to(0)
    assert_that(review_calls["run_review"]).is_equal_to(0)
    assert_that(result.output).contains("already reviewed at sha9 in round 2")
    assert_that(result.output).contains("3 files covered, 1 open finding carried")
    assert_that(seen["force_full"]).is_false()


@pytest.mark.usefixtures("patched_review")
def test_json_output_emits_the_already_reviewed_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skipped round is machine-readable, and is not a review envelope."""
    _with_prior_state(monkeypatch=monkeypatch, state=_state())
    _decide(monkeypatch=monkeypatch, decision=_DECISION)

    result = CliRunner().invoke(review_command, ["--output", "json"])
    payload = _envelope(output=result.output)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(payload["outcome"]).is_equal_to("already_reviewed")
    assert_that(payload["already_reviewed"]).is_equal_to(
        {
            "head_sha": _HEAD,
            "round": 2,
            "files_covered": 3,
            "open_findings": 1,
            "open_p1": 0,
        },
    )
    assert_that(payload["detail"]).contains("already reviewed at sha9")
    assert_that(payload).does_not_contain_key("readiness_verdict")
    assert_that(payload).does_not_contain_key("findings")
    assert_that(payload).does_not_contain_key("partial")


@pytest.mark.usefixtures("patched_review")
def test_an_open_p1_record_exits_one_like_a_converged_skip(
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skip reports what is still open; the check greens it and names it."""
    _with_prior_state(monkeypatch=monkeypatch, state=_state(_open_p1()))
    _decide(monkeypatch=monkeypatch, decision=_DECISION)

    result = CliRunner().invoke(review_command, ["--output", "json"])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(review_calls["run_review"]).is_equal_to(0)
    assert_that(
        _envelope(output=result.output)["already_reviewed"]["open_p1"],
    ).is_equal_to(1)


@pytest.mark.usefixtures("patched_review")
def test_a_head_the_guard_does_not_recognise_reviews(
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the guard answers None the round runs as it always did."""
    _with_prior_state(monkeypatch=monkeypatch, state=_state())
    _decide(monkeypatch=monkeypatch, decision=None)

    result = CliRunner().invoke(review_command, [])

    assert_that(review_calls["run_review"]).is_equal_to(1)
    assert_that(result.exception).is_instance_of(_RoundRanError)


@pytest.mark.usefixtures("patched_review")
def test_full_never_consults_the_guard(
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--full`` is the always-available manual override."""
    _with_prior_state(monkeypatch=monkeypatch, state=_state())
    seen = _decide(monkeypatch=monkeypatch, decision=_DECISION)

    result = CliRunner().invoke(review_command, ["--full"])

    assert_that(seen).is_empty()
    assert_that(review_calls["run_review"]).is_equal_to(1)
    assert_that(result.exception).is_instance_of(_RoundRanError)


@pytest.mark.usefixtures("patched_review")
def test_post_without_publication_evidence_reviews_again(
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A covered head whose review never reached the PR is reviewed so it can."""
    _with_prior_state(monkeypatch=monkeypatch, state=_state())
    _decide(monkeypatch=monkeypatch, decision=_DECISION)
    reporter = MagicMock()
    reporter.find_issue_comment.return_value = None
    monkeypatch.setattr(
        "lintro.ai.integrations.github_pr.GitHubPRReporter",
        lambda **_: reporter,
    )

    result = CliRunner().invoke(
        review_command,
        ["--post", "--pr", "7", "--repo", "o/r"],
    )

    reporter.find_issue_comment.assert_called_once()
    assert_that(review_calls["run_review"]).is_equal_to(1)
    assert_that(result.exception).is_instance_of(_RoundRanError)


@pytest.mark.usefixtures("patched_review")
def test_post_with_publication_evidence_skips_and_posts_nothing(
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the sticky present and threads in place, the skip touches nothing."""
    _with_prior_state(monkeypatch=monkeypatch, state=_state(_open_p1()))
    _decide(monkeypatch=monkeypatch, decision=_DECISION)
    reporter = MagicMock()
    reporter.find_issue_comment.return_value = (1, "<!-- lintro-ai-review -->")
    monkeypatch.setattr(
        "lintro.ai.integrations.github_pr.GitHubPRReporter",
        lambda **_: reporter,
    )
    posted: list[object] = []
    monkeypatch.setattr(
        "lintro.ai.review.github.post_review_to_github",
        lambda **kwargs: posted.append(kwargs),
    )

    result = CliRunner().invoke(
        review_command,
        ["--post", "--pr", "7", "--repo", "o/r", "--output", "json"],
    )

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(review_calls["get_provider"]).is_equal_to(0)
    assert_that(review_calls["run_review"]).is_equal_to(0)
    assert_that(posted).is_empty()
    assert_that(reporter.method_calls).is_length(1)
    assert_that(_envelope(output=result.output)["outcome"]).is_equal_to(
        "already_reviewed",
    )


def test_the_force_variable_gets_past_both_skips(
    patched_review: MagicMock,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_REVIEW_FORCE=1`` runs a round past the convergence skip too."""
    from lintro.ai.review.models.run_outcome import RunOutcome

    config = patched_review
    config.review.convergence = ReviewConvergenceConfig(threshold=3.0, stable_rounds=2)
    quiet = ReviewState(
        runs=tuple(
            RunRecord(
                identity=RunIdentity(round=index, sha=_HEAD),
                outcome=RunOutcome(convergence_score=0.5),
            )
            for index in (1, 2)
        ),
    )
    _with_prior_state(monkeypatch=monkeypatch, state=quiet)
    seen = _decide(monkeypatch=monkeypatch, decision=_DECISION)
    monkeypatch.setenv(guard_module.FORCE_ENV, "1")

    result = CliRunner().invoke(review_command, [])

    assert_that(seen).is_empty()
    assert_that(review_calls["run_review"]).is_equal_to(1)
    assert_that(result.exception).is_instance_of(_RoundRanError)
