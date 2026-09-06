"""Wiring tests: the convergence stop rule short-circuits a review (#2099)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.review import preparation as preparation_module
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.flagged_file import FlaggedFile
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
        raise _RoundRanError

    monkeypatch.setattr(review_module, "require_ai", lambda: None)
    monkeypatch.setattr(review_module, "get_config", lambda: config)
    monkeypatch.setattr(
        preparation_module,
        "collect_review_context",
        lambda **_: MagicMock(changed_files=[], head_ref="sha9"),
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
    return convergence


class _RoundRanError(Exception):
    """Raised by the ``run_review`` stub to mark that a round really started.

    A distinct type, not a bare ``AssertionError``: ``CliRunner.invoke``
    swallows every exception by default, so a stub that aborted with a
    generic error would be indistinguishable from a ``TypeError`` raised by a
    changed ``run_review`` signature, or from a crash immediately after entry.
    ``_assert_round_ran`` checks for this type specifically, so only a real
    call through the real seam satisfies a still-reviews test (#2099 review).
    """


def _assert_round_ran(*, result: object, review_calls: dict[str, int]) -> None:
    """Assert the command actually reached ``run_review`` and aborted there.

    Args:
        result: ``CliRunner`` result from the invocation.
        review_calls: Collaborator call counter.
    """
    assert_that(review_calls["run_review"]).is_equal_to(1)
    assert_that(result.exception).is_instance_of(_RoundRanError)  # type: ignore[attr-defined]


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

    result = CliRunner().invoke(review_command, [])

    _assert_round_ran(result=result, review_calls=review_calls)


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

    result = CliRunner().invoke(review_command, ["--full"])

    _assert_round_ran(result=result, review_calls=review_calls)


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

    result = CliRunner().invoke(review_command, [])

    _assert_round_ran(result=result, review_calls=review_calls)


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
    posted: list[dict[str, object]] = []

    def _post(**kwargs: object) -> bool:
        posted.append(kwargs)
        return True

    monkeypatch.setattr(
        "lintro.ai.review.github.post_review_converged_to_github",
        _post,
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")

    result = CliRunner().invoke(review_command, ["--post"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(seen["pr_number"]).is_equal_to(42)
    # The seam itself is asserted, not just stubbed: a converged path that
    # never posted would otherwise pass this test (#2099 review).
    assert_that(posted).is_length(1)
    assert_that(posted[0]["pr_number"]).is_equal_to(42)
    assert_that(posted[0]["repo"]).is_equal_to("owner/name")
    decision = posted[0]["decision"]
    assert isinstance(decision, ConvergenceDecision)
    assert_that(decision.converged).is_true()


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

    result = CliRunner().invoke(review_command, [])

    assert_that(review_calls["get_provider"]).is_equal_to(1)
    _assert_round_ran(result=result, review_calls=review_calls)


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

    result = CliRunner().invoke(review_command, [])

    assert_that(review_calls["get_provider"]).is_equal_to(1)
    _assert_round_ran(result=result, review_calls=review_calls)


@pytest.mark.parametrize(
    "ledger",
    [
        {"flagged_files": (FlaggedFile(path="a.py", reason="re-read"),)},
        {"pending_invalidations": (("b.py", "group_invalidated"),)},
    ],
    ids=["model-flagged file", "unserved invalidation"],
)
def test_pending_resume_work_forces_a_round_despite_a_quiet_window(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    ledger: dict[str, object],
) -> None:
    """Queued resume work is deferred by a skip, not dropped — so it blocks it.

    The run window here is byte-identical to the one that converges in
    ``test_a_quiet_streak_skips_the_round``; only the #2154 ledger differs. A
    skip would leave the flagged or invalidated file unreviewed with nothing
    left to queue it, because the skip writes no state of its own.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
        ledger: Pending resume-work slice under test.
    """
    del patched_review
    quiet = _quiet_state(scores=(1.0, 0.5))
    _with_prior_state(
        monkeypatch=monkeypatch,
        state=ReviewState(runs=quiet.runs, **ledger),  # type: ignore[arg-type]
    )

    result = CliRunner().invoke(review_command, [])

    assert_that(review_calls["get_provider"]).is_equal_to(1)
    _assert_round_ran(result=result, review_calls=review_calls)


def test_a_blocking_skip_still_stamps_the_board_before_it_exits(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exiting 1 must not leave the last board unbannered.

    Posting and the open-P1 exit are otherwise covered by separate tests, so
    an implementation that raised before posting would pass both. A red CI
    check whose sticky never says why the round was skipped is the worst of
    both: no review, and no explanation on the PR.

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
    _with_prior_state(
        monkeypatch=monkeypatch,
        state=ReviewState(
            runs=quiet.runs,
            findings=(
                FindingRecord(
                    fingerprint="p1",
                    severity=Severity.P1,
                    status=FindingStatus.OPEN,
                ),
            ),
        ),
    )
    monkeypatch.setattr(review_module, "_detect_pr_number_from_env", lambda: 42)
    posted: list[dict[str, object]] = []

    def _post(**kwargs: object) -> bool:
        posted.append(kwargs)
        return True

    monkeypatch.setattr(
        "lintro.ai.review.github.post_review_converged_to_github",
        _post,
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")

    result = CliRunner().invoke(review_command, ["--post"])

    assert_that(review_calls["get_provider"]).is_equal_to(0)
    assert_that(posted).is_length(1)
    assert_that(result.exit_code).is_equal_to(1)


def test_a_converged_skip_ignores_an_open_p1_question(
    patched_review: ReviewConvergenceConfig,
    review_calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A question never blocks the skip, whatever severity it carries.

    The sibling below pins the same window with a FINDING-kind P1 exiting 1;
    this is the other half of that pair, so the two together show the gate
    keys on kind rather than on severity alone.

    Args:
        patched_review: Convergence config the command reads.
        review_calls: Collaborator call counter.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.ai.review.enums.finding_kind import FindingKind
    from lintro.ai.review.enums.finding_status import FindingStatus
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_finding import Severity

    del patched_review
    quiet = _quiet_state(scores=(1.0, 0.5))
    _with_prior_state(
        monkeypatch=monkeypatch,
        state=ReviewState(
            runs=quiet.runs,
            findings=(
                FindingRecord(
                    fingerprint="q1",
                    severity=Severity.P1,
                    status=FindingStatus.OPEN,
                    kind=FindingKind.QUESTION,
                ),
            ),
        ),
    )

    result = CliRunner().invoke(review_command, ["--output", "json"])

    assert_that(review_calls["get_provider"]).is_equal_to(0)
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(_envelope(output=result.output)["converged"]["open_p1"]).is_equal_to(0)


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
    assert_that(_envelope(output=result.output)["converged"]["open_p1"]).is_equal_to(1)
