"""Tests for classify-nightly-dogfood-failure.py (#2246).

Covers the binding state table from the issue: a runner kill that a bounded
retry answered must not reach the tracker, everything else must — including
anything the classifier cannot classify.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "classify-nightly-dogfood-failure.py"
_INFRA_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "is-infra-flake-failure.sh"


def _load_module() -> ModuleType:
    """Load classify-nightly-dogfood-failure.py as an importable module.

    Returns:
        The loaded module.

    Raises:
        RuntimeError: If the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location(
        "classify_nightly_dogfood_failure",
        _SCRIPT,
    )
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    """Provide the loaded classifier module.

    Returns:
        The loaded module.
    """
    return _load_module()


def _decide(module: ModuleType, **environ: str) -> Any:
    """Run the whole decision for one simulated nightly run.

    Args:
        module: The loaded classifier module.
        **environ: Workflow environment values (e.g. ``LINT_RESULT``).

    Returns:
        The module's ``Decision`` for the simulated run.
    """
    base = {
        "LINT_RESULT": "success",
        "SKIP_GATE_RESULT": "success",
        "VERIFY_RESULT": "success",
    }
    base.update(environ)
    units = module.build_units(environ=base)
    return module.decide(units, script=_INFRA_SCRIPT)


def test_all_green_run_never_pings(module: ModuleType) -> None:
    """A fully successful nightly leaves the tracker alone."""
    decision = _decide(module)

    assert_that(decision.notify).is_false()
    assert_that(decision.annotation).is_empty()


def test_genuine_lint_failure_pings_exactly_as_before(module: ModuleType) -> None:
    """A real lint verdict is reported, and never softened to a coverage gap."""
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_STATUS="failed",
        LINT_EXIT_CODE="1",
    )

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_true()
    assert_that(decision.annotation).is_empty()
    assert_that(decision.reason).contains("dogfood-full")


def test_runner_kill_answered_by_the_retry_is_silent(module: ModuleType) -> None:
    """Runner shutdown (exit 143) plus a passing retry produces zero pings."""
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_EXIT_CODE="143",
        LINT_RETRY_RESULT="success",
    )

    assert_that(decision.notify).is_false()


def test_runner_kill_then_genuine_retry_failure_pings(module: ModuleType) -> None:
    """A retry that finds real issues is a regression, not absorbed noise."""
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_EXIT_CODE="143",
        LINT_RETRY_RESULT="failure",
        LINT_RETRY_STATUS="failed",
        LINT_RETRY_EXIT_CODE="1",
    )

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_true()
    assert_that(decision.annotation).is_empty()


def test_two_runner_kills_ping_without_demanding_action(module: ModuleType) -> None:
    """Two kills leave no verdict: ping, annotated, no action-required framing."""
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_EXIT_CODE="143",
        LINT_RETRY_RESULT="failure",
        LINT_RETRY_EXIT_CODE="143",
    )

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_false()
    assert_that(decision.annotation).contains("no lint verdict tonight")
    assert_that(decision.annotation).contains("superseded")


def test_cancelled_lint_job_is_treated_as_a_kill(module: ModuleType) -> None:
    """A cancelled job carries no lint verdict, so the retry decides."""
    decision = _decide(
        module,
        LINT_RESULT="cancelled",
        LINT_RETRY_RESULT="success",
    )

    assert_that(decision.notify).is_false()


def test_killed_lint_without_a_retry_fails_closed(module: ModuleType) -> None:
    """A kill nobody retried still pings: a silent uncovered night is worse."""
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_EXIT_CODE="143",
        LINT_RETRY_RESULT="skipped",
    )

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_true()


def test_skip_gate_kill_without_outputs_is_answered_by_its_retry(
    module: ModuleType,
) -> None:
    """A gate killed before publishing a verdict is cleared by a passing retry.

    This is the observed nightly signature: the runner dies mid-lint, so the
    job fails with empty ``status``/``exit-code`` outputs.
    """
    decision = _decide(
        module,
        SKIP_GATE_RESULT="failure",
        SKIP_GATE_RETRY_RESULT="success",
    )

    assert_that(decision.notify).is_false()


def test_skip_gate_verdict_failure_is_never_absorbed(module: ModuleType) -> None:
    """A real non-allowlisted skip pings even if a retry somehow passed."""
    decision = _decide(
        module,
        SKIP_GATE_RESULT="failure",
        SKIP_GATE_STATUS="failed",
        SKIP_GATE_EXIT_CODE="1",
        SKIP_GATE_RETRY_RESULT="success",
    )

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_true()


def test_pinned_digest_failure_always_pings(module: ModuleType) -> None:
    """The digest-lag verifier has no retry, so its failure always reports."""
    decision = _decide(module, VERIFY_RESULT="failure")

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_true()
    assert_that(decision.reason).contains("verify-pinned-image-tools")


def test_a_real_failure_beside_a_kill_keeps_action_required(
    module: ModuleType,
) -> None:
    """One unanswered kill must not soften a genuine failure elsewhere."""
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_EXIT_CODE="143",
        LINT_RETRY_RESULT="failure",
        LINT_RETRY_EXIT_CODE="143",
        VERIFY_RESULT="failure",
    )

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_true()
    assert_that(decision.annotation).is_empty()


def test_tool_execution_timeout_is_absorbed_like_the_pr_gate(
    module: ModuleType,
) -> None:
    """The attempt's own zero-findings timeout verdict is infra, not a regression.

    Same evidence the code-quality gate absorbs on since #2242: the flag comes
    from the authoritative attempt's own JSON report (#1653, lgtm-ci#746).
    """
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_STATUS="failed",
        LINT_EXIT_CODE="1",
        LINT_TIMEOUT_FLAKE="true",
        LINT_TIMED_OUT_TOOLS="semgrep",
        LINT_RETRY_RESULT="success",
    )

    assert_that(decision.notify).is_false()


def test_tool_execution_timeout_without_a_retry_fails_closed(
    module: ModuleType,
) -> None:
    """A timeout-flake primary is infra, so a skipped retry must still ping.

    The workflow retries on ``timeout-flake == 'true'`` precisely so this
    branch is only reached when the retry job itself failed to start.
    """
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_STATUS="failed",
        LINT_EXIT_CODE="1",
        LINT_TIMEOUT_FLAKE="true",
        LINT_TIMED_OUT_TOOLS="semgrep",
        LINT_RETRY_RESULT="skipped",
    )

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_true()


def test_broken_shared_classifier_is_an_error_not_a_verdict(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exit code other than 0/1 from the shared classifier is fatal.

    Treating a crash as "not infra" would let an empty-output kill fall
    through to NO_VERDICT, after which a passing retry would silence the
    night; the classifier must instead fail closed with exit code 2.
    """
    broken = tmp_path / "is-infra-flake-failure.sh"
    broken.write_text("#!/usr/bin/env bash\necho boom >&2\nexit 3\n")
    broken.chmod(0o755)
    monkeypatch.setenv("INFRA_FLAKE_SCRIPT", str(broken))
    monkeypatch.setenv("LINT_RESULT", "failure")
    monkeypatch.setenv("LINT_RETRY_RESULT", "success")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))

    attempt = module.Attempt(
        result="failure",
        conclusion="",
        status="",
        exit_code="",
        timeout_flake="",
        timed_out_tools="",
    )
    with pytest.raises(RuntimeError):
        module.is_infra_flake(attempt, script=broken)
    assert_that(module.main(argv=[])).is_equal_to(2)


def test_partial_outputs_are_unclassifiable_and_ping(module: ModuleType) -> None:
    """A half-written verdict is never absorbed — absence of proof is not proof."""
    decision = _decide(
        module,
        LINT_RESULT="failure",
        LINT_STATUS="passed",
        LINT_RETRY_RESULT="success",
    )

    assert_that(decision.notify).is_true()
    assert_that(decision.action_required).is_true()


def test_attempt_states_cover_the_observed_signatures(module: ModuleType) -> None:
    """Each same-run signature maps to the attempt state the table expects."""
    states = module.AttemptState
    classify = module.classify_attempt

    assert_that(
        classify(module.Attempt(result="success"), script=_INFRA_SCRIPT),
    ).is_equal_to(states.PASSED)
    assert_that(
        classify(module.Attempt(result="skipped"), script=_INFRA_SCRIPT),
    ).is_equal_to(states.NOT_RUN)
    assert_that(
        classify(
            module.Attempt(result="failure", exit_code="143"),
            script=_INFRA_SCRIPT,
        ),
    ).is_equal_to(states.INFRA)
    assert_that(
        classify(module.Attempt(result="failure"), script=_INFRA_SCRIPT),
    ).is_equal_to(states.NO_VERDICT)
    assert_that(
        classify(
            module.Attempt(result="failure", status="failed", exit_code="1"),
            script=_INFRA_SCRIPT,
        ),
    ).is_equal_to(states.GENUINE)
    assert_that(
        classify(
            module.Attempt(result="failure", status="passed"),
            script=_INFRA_SCRIPT,
        ),
    ).is_equal_to(states.UNCLASSIFIABLE)


def test_main_writes_outputs_and_summary(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entry point publishes its decision to GITHUB_OUTPUT and the summary."""
    output = tmp_path / "output.txt"
    summary = tmp_path / "summary.md"
    for key in (
        "LINT_STATUS",
        "LINT_EXIT_CODE",
        "LINT_RETRY_RESULT",
        "SKIP_GATE_STATUS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("INFRA_FLAKE_SCRIPT", str(_INFRA_SCRIPT))
    monkeypatch.setenv("LINT_RESULT", "success")
    monkeypatch.setenv("SKIP_GATE_RESULT", "failure")
    monkeypatch.setenv("SKIP_GATE_RETRY_RESULT", "success")
    monkeypatch.setenv("VERIFY_RESULT", "success")

    assert_that(module.main(argv=[])).is_equal_to(0)

    written = output.read_text(encoding="utf-8")
    assert_that(written).contains("notify=false")
    assert_that(written).contains("action-required=false")
    assert_that(summary.read_text(encoding="utf-8")).contains("Tracker ping")


def test_main_fails_when_the_shared_classifier_is_missing(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing shared classifier is a usage error, never a silent 'no ping'."""
    monkeypatch.setenv("INFRA_FLAKE_SCRIPT", str(tmp_path / "absent.sh"))

    assert_that(module.main(argv=[])).is_equal_to(2)
