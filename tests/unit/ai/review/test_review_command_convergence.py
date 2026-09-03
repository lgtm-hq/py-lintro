"""Wiring tests: the convergence stop rule short-circuits a review (#2099)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.cli_utils.commands import review as review_module
from lintro.cli_utils.commands.review import review_command
from lintro.config.review_config import ReviewConvergenceConfig


def _quiet_state(*, scores: tuple[float | None, ...]) -> ReviewState:
    """Build prior state whose rounds carry the given scores.

    Args:
        scores: Recorded convergence score per round, oldest first.

    Returns:
        The prior review state.
    """
    return ReviewState(
        runs=tuple(
            RunRecord(round=index, sha=f"sha{index}", convergence_score=score)
            for index, score in enumerate(scores, start=1)
        ),
    )


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
) -> ReviewConvergenceConfig:
    """Stub the review command up to the provider call.

    The provider and orchestrator are counted rather than stubbed silently:
    "no provider call was made" is the whole point of the stop rule, and a
    test that only checked the exit code would pass even if the round ran.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        review_calls: Counter shared with the tests.

    Returns:
        The mutable convergence config the command will read.
    """
    from lintro.ai.config import AIConfig

    convergence = ReviewConvergenceConfig(threshold=3.0, stable_rounds=2)
    config = MagicMock()
    config.ai = {"enabled": True, "review": True, "provider": "anthropic"}
    config.review.depth = 1
    config.review.strictness = ReviewStrictness.BALANCED
    config.review.sensitivity = {}
    config.review.checklist_display = "off"
    config.review.force_semantic_chunking = False
    config.review.custom_agents = CustomAgentMode.DISABLED
    config.review.convergence = convergence

    def _count_provider(*_args: object, **_kwargs: object) -> MagicMock:
        review_calls["get_provider"] += 1
        return MagicMock(name="anthropic")

    def _count_run_review(*_args: object, **_kwargs: object) -> MagicMock:
        review_calls["run_review"] += 1
        raise AssertionError("the round should have been short-circuited")

    monkeypatch.setattr(review_module, "require_ai", lambda: None)
    monkeypatch.setattr(review_module, "get_config", lambda: config)
    monkeypatch.setattr(
        review_module,
        "collect_review_context",
        lambda **_: MagicMock(changed_files=[], head_ref="sha9"),
    )
    monkeypatch.setattr(review_module, "classify_changed_files", lambda _: [])
    monkeypatch.setattr(review_module, "get_all_checklist_items", lambda **_: [])
    monkeypatch.setattr(review_module, "select_checklist_items", lambda **_: [])
    monkeypatch.setattr(
        review_module,
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
        "apply_cli_overrides",
        lambda _resolved, **_kwargs: AIConfig.resolve_from_mapping(
            {"enabled": True, "review": True, "provider": "anthropic"},
        ),
    )
    monkeypatch.setattr(
        review_module,
        "resolve_sensitivity_policy",
        lambda **_: MagicMock(),
    )
    monkeypatch.setattr(review_module, "get_provider", _count_provider)
    monkeypatch.setattr(review_module, "run_review", _count_run_review)
    return convergence


def _with_prior_state(
    *,
    monkeypatch: pytest.MonkeyPatch,
    state: ReviewState,
) -> None:
    """Make the command load a fixed prior state.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        state: State to return from the loader.
    """
    monkeypatch.setattr(
        review_module,
        "_load_prior_review_state",
        lambda **_: state,
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


def test_two_quiet_rounds_skip_the_round_without_calling_the_provider(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stop rule fires before anything is spent.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    del patched_review
    _with_prior_state(monkeypatch=monkeypatch, state=_quiet_state(scores=(1.0, 0.5)))

    result = CliRunner().invoke(review_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(review_calls["get_provider"]).is_equal_to(0)
    assert_that(review_calls["run_review"]).is_equal_to(0)
    assert_that(result.output).contains(
        "converged at round 3 (score 0.50 < threshold 3.00)",
    )
    assert_that(result.output).contains("Score trajectory: 1.00 → 0.50")


def test_a_single_quiet_round_still_reviews(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One quiet round is not the configured streak.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    del patched_review
    _with_prior_state(monkeypatch=monkeypatch, state=_quiet_state(scores=(9.0, 0.5)))

    CliRunner().invoke(review_command, [])

    assert_that(review_calls["run_review"]).is_equal_to(1)


def test_the_rule_is_disabled_by_default(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no threshold configured, every round reviews as it always did.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    default = ReviewConvergenceConfig()
    patched_review.threshold = default.threshold
    patched_review.stable_rounds = default.stable_rounds
    _with_prior_state(monkeypatch=monkeypatch, state=_quiet_state(scores=(0.0, 0.0)))

    CliRunner().invoke(review_command, [])

    assert_that(default.threshold).is_none()
    assert_that(review_calls["run_review"]).is_equal_to(1)


def test_full_forces_a_round_through_the_stop_rule(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--full`` is the always-available manual override.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    del patched_review
    _with_prior_state(monkeypatch=monkeypatch, state=_quiet_state(scores=(1.0, 0.5)))

    CliRunner().invoke(review_command, ["--full"])

    assert_that(review_calls["run_review"]).is_equal_to(1)


def test_json_output_emits_the_converged_envelope(
    patched_review: ReviewConvergenceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skipped round is machine-readable, and is not a review envelope.

    Args:
        patched_review: Convergence config the command reads.
        monkeypatch: Pytest monkeypatch fixture.
    """
    del patched_review
    _with_prior_state(monkeypatch=monkeypatch, state=_quiet_state(scores=(1.0, 0.5)))

    result = CliRunner().invoke(review_command, ["--output", "json"])
    payload = _envelope(output=result.output)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(payload["outcome"]).is_equal_to("converged")
    assert_that(payload["converged"]["round"]).is_equal_to(3)
    assert_that(payload["converged"]["score"]).is_equal_to(0.5)
    assert_that(payload["converged"]["threshold"]).is_equal_to(3.0)
    assert_that(payload["converged"]["trajectory"]).is_equal_to([1.0, 0.5])
    assert_that(payload["converged"]["stable_rounds"]).is_equal_to(2)
    assert_that(payload["converged"]["open_p1"]).is_equal_to(0)
    # A round that never ran must not look like one that reviewed and found
    # nothing, nor like a run that stopped early with work left undone.
    assert_that(payload).does_not_contain_key("readiness_verdict")
    assert_that(payload).does_not_contain_key("findings")
    assert_that(payload).does_not_contain_key("partial")


def test_a_degraded_prior_round_still_reviews(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped round's low score is not evidence the review is settled.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    del patched_review
    state = ReviewState(
        runs=(
            RunRecord(round=1, convergence_score=0.5),
            RunRecord(round=2, convergence_score=0.5, partial=True),
        ),
    )
    _with_prior_state(monkeypatch=monkeypatch, state=state)

    CliRunner().invoke(review_command, [])

    assert_that(review_calls["run_review"]).is_equal_to(1)


def test_post_loads_state_for_the_pr_detected_from_ci(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --post and no --pr, state is looked up for the CI-detected PR.

    The stop rule reads prior rounds from persisted state; if the lookup used
    the absent ``--pr`` value instead of the detected one, a CI review could
    never see its own history and would never converge.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import lintro.cli_utils.commands.review as review_module

    del patched_review, review_calls
    seen: dict[str, object] = {}

    def _load(**kwargs: object) -> ReviewState:
        seen.update(kwargs)
        return _quiet_state(scores=(1.0, 0.5))

    monkeypatch.setattr(review_module, "_load_prior_review_state", _load)
    monkeypatch.setattr(review_module, "_detect_pr_number_from_env", lambda: 42)
    monkeypatch.setattr(
        "lintro.ai.review.github.post_review_converged_to_github",
        lambda **kwargs: True,
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")

    result = CliRunner().invoke(review_command, ["--post"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(seen["pr_number"]).is_equal_to(42)


def test_a_coverage_limited_prior_round_still_reviews(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped round cannot attest stability, so the provider still runs.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    del patched_review
    state = ReviewState(
        runs=(
            RunRecord(round=1, convergence_score=0.5),
            RunRecord(round=2, convergence_score=0.5, coverage_limited=True),
        ),
    )
    _with_prior_state(monkeypatch=monkeypatch, state=state)

    CliRunner().invoke(review_command, [])

    assert_that(review_calls["get_provider"]).is_equal_to(1)


def test_a_score_equal_to_the_threshold_still_reviews(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quiet is strictly below the threshold.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    threshold = patched_review.threshold
    assert threshold is not None
    _with_prior_state(
        monkeypatch=monkeypatch,
        state=_quiet_state(scores=(threshold, threshold)),
    )

    CliRunner().invoke(review_command, [])

    assert_that(review_calls["get_provider"]).is_equal_to(1)


def test_a_converged_skip_keeps_an_open_p1_blocking(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping a round never relaxes the readiness gate the last round set.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.ai.review.enums.finding_status import FindingStatus
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_finding import Severity

    del patched_review
    quiet = _quiet_state(scores=(1.0, 0.5))
    state = ReviewState(
        runs=quiet.runs,
        findings=(
            FindingRecord(
                fingerprint="p1",
                severity=Severity.P1,
                status=FindingStatus.OPEN,
            ),
        ),
    )
    _with_prior_state(monkeypatch=monkeypatch, state=state)

    result = CliRunner().invoke(review_command, ["--output", "json"])

    assert_that(review_calls["get_provider"]).is_equal_to(0)
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(json.loads(result.output)["converged"]["open_p1"]).is_equal_to(1)
