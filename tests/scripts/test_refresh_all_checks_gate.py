# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the stale All Checks Gate refresh after a successful retry."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "refresh-all-checks-gate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "auto-rerun-on-infra-failure.yml"
REPO = "lgtm-hq/py-lintro"
SHA = "2e22dcc5de1b493f12ba2eb49c89869a4ec04adb"
OTHER_SHA = "24c5db7f" + "0" * 32
GATE_PATH = ".github/workflows/all-checks-gate.yml"
RECOVERED_AT = "2026-11-04T05:47:00Z"


def _load() -> Any:
    """Load the hyphenated script as a module."""
    spec = importlib.util.spec_from_file_location("refresh_all_checks_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["refresh_all_checks_gate"] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _trigger(**overrides: Any) -> dict[str, Any]:
    """Return a successful attempt-2 CI - Docker run on ``SHA``."""
    run = {
        "id": 41776692501,
        "name": "CI - Docker",
        "path": ".github/workflows/docker-ci.yml",
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 2,
        "updated_at": RECOVERED_AT,
        "head_repository": {"full_name": REPO},
    }
    run.update(overrides)
    return run


def _gate(gate_id: int, **overrides: Any) -> dict[str, Any]:
    """Return a red gate run on ``SHA`` that finished before the recovery."""
    run = {
        "id": gate_id,
        "name": "PR - All Checks Gate",
        "path": GATE_PATH,
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "failure",
        "run_attempt": 1,
        "updated_at": "2026-11-04T05:19:39Z",
    }
    run.update(overrides)
    return run


class FakeGitHub:
    """In-memory ``gh api`` stand-in that records every rerun POST."""

    def __init__(
        self,
        *,
        trigger: dict[str, Any],
        gates: list[dict[str, Any]],
        failing_posts: frozenset[int] = frozenset(),
    ) -> None:
        """Seed the fake with one trigger run and a list of gate runs."""
        self.runs = {trigger["id"]: trigger, **{g["id"]: g for g in gates}}
        self.gates = gates
        self.failing_posts = failing_posts
        self.pulls: dict[int, dict[str, Any]] = {}
        self.reran: list[int] = []

    def __call__(self, *args: str) -> Any:
        """Serve the subset of endpoints the script calls."""
        if args[:2] == ("--method", "POST"):
            gate_id = int(args[2].split("/")[-2])
            if gate_id in self.failing_posts:
                raise RuntimeError("HTTP 500")
            self.reran.append(gate_id)
            return None
        (path,) = args
        if "/workflows/all-checks-gate.yml/runs?" in path:
            return {"workflow_runs": self.gates if "page=1" in path else []}
        if "/actions/runs?" in path:
            listed = list(self.runs.values()) if "page=1" in path else []
            return {"workflow_runs": listed}
        if "/pulls/" in path:
            return self.pulls[int(path.rsplit("/", 1)[-1])]
        return self.runs[int(path.rsplit("/", 1)[-1])]


def test_reruns_the_red_gate_after_a_successful_retry() -> None:
    """The 2026-11-04 case: Docker attempt 2 passed, the gate stayed red."""
    trigger = _trigger()
    gh = FakeGitHub(trigger=trigger, gates=[_gate(41776692371)])

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(0)
    assert_that(gh.reran).is_equal_to([41776692371])


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_attempt": 1},
        {"conclusion": "failure"},
        {"status": "in_progress", "conclusion": None},
        {"path": GATE_PATH, "name": "PR - All Checks Gate"},
    ],
    ids=["first-attempt", "retry-failed", "not-finished", "gate-itself"],
)
def test_ignores_events_that_are_not_a_recovery(overrides: dict[str, Any]) -> None:
    """Only a finished, green attempt 2+ of another workflow may refresh."""
    trigger = _trigger(**overrides)
    gh = FakeGitHub(trigger=trigger, gates=[_gate(1)])

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(0)
    assert_that(gh.reran).is_empty()


@pytest.mark.parametrize(
    "overrides",
    [
        {"conclusion": "success"},
        {"status": "in_progress", "conclusion": None},
        {"head_sha": OTHER_SHA},
        {"path": ".github/workflows/other.yml"},
        {"updated_at": RECOVERED_AT},
        {"updated_at": "2026-11-04T06:00:00Z"},
    ],
    ids=["green", "running", "other-sha", "other-file", "same-second", "newer"],
)
def test_leaves_gates_the_recovery_did_not_make_stale(
    overrides: dict[str, Any],
) -> None:
    """A gate that already judged the recovered attempt keeps its verdict."""
    trigger = _trigger()
    gh = FakeGitHub(trigger=trigger, gates=[_gate(7, **overrides)])

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(0)
    assert_that(gh.reran).is_empty()


def test_skips_a_replayed_event_whose_run_was_retried_again() -> None:
    """The API, not the event payload, decides whether the retry is current."""
    trigger = _trigger()
    gh = FakeGitHub(trigger=trigger, gates=[_gate(7)])
    gh.runs[trigger["id"]] = _trigger(run_attempt=3, conclusion="failure")

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(0)
    assert_that(gh.reran).is_empty()


def test_a_displaced_later_recovery_still_sets_the_cutoff() -> None:
    """Out-of-order events: the survivor must not drop the newest recovery.

    CI - Tests recovered at 05:47 and its pending refresh was displaced by a
    delayed event for CI - Docker, which recovered at 05:40. A gate that went
    red at 05:45 judged the failed CI - Tests attempt and must be re-run,
    although it finished after the surviving event's own recovery.
    """
    trigger = _trigger(updated_at="2026-11-04T05:40:00Z")
    gh = FakeGitHub(
        trigger=trigger,
        gates=[_gate(7, updated_at="2026-11-04T05:45:00Z")],
    )
    later = _trigger(
        id=41776699999,
        name="CI - Tests",
        path=".github/workflows/test-ci.yml",
        updated_at="2026-11-04T05:47:00Z",
    )
    gh.runs[later["id"]] = later

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(0)
    assert_that(gh.reran).is_equal_to([7])


@pytest.mark.parametrize(
    "overrides",
    [
        {"head_repository": {"full_name": "someone/py-lintro"}},
        {"head_sha": OTHER_SHA},
        {"run_attempt": 1},
    ],
    ids=["fork-run", "other-sha", "first-attempt"],
)
def test_only_same_repo_retries_on_the_sha_move_the_cutoff(
    overrides: dict[str, Any],
) -> None:
    """A later green run that is not a same-repo retry on the SHA is ignored."""
    trigger = _trigger(updated_at="2026-11-04T05:40:00Z")
    gh = FakeGitHub(
        trigger=trigger,
        gates=[_gate(7, updated_at="2026-11-04T05:45:00Z")],
    )
    other = _trigger(id=41776699999, updated_at="2026-11-04T05:47:00Z", **overrides)
    gh.runs[other["id"]] = other

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(0)
    assert_that(gh.reran).is_empty()


def _pr_gate(gate_id: int, number: int) -> dict[str, Any]:
    """Return a red ``pull_request`` gate on ``SHA`` for PR ``number``."""
    return _gate(gate_id, event="pull_request", pull_requests=[{"number": number}])


@pytest.mark.parametrize(
    ("pull", "expected"),
    [
        ({"state": "open", "head": {"sha": SHA}}, [7]),
        ({"state": "open", "head": {"sha": OTHER_SHA}}, []),
        ({"state": "closed", "head": {"sha": SHA}}, []),
    ],
    ids=["head-current", "head-moved", "pr-closed"],
)
def test_pr_gate_is_rerun_only_while_its_sha_is_the_pr_head(
    pull: dict[str, Any],
    expected: list[int],
) -> None:
    """An old head's rerun would cancel the live gate in the per-PR group."""
    trigger = _trigger()
    gh = FakeGitHub(trigger=trigger, gates=[_pr_gate(7, 3176)])
    gh.pulls[3176] = pull

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(0)
    assert_that(gh.reran).is_equal_to(expected)


def test_pr_gate_without_a_pr_reference_is_left_alone() -> None:
    """With no PR to compare against, the head cannot be proven current."""
    trigger = _trigger()
    gh = FakeGitHub(
        trigger=trigger,
        gates=[_gate(7, event="pull_request", pull_requests=[])],
    )

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(0)
    assert_that(gh.reran).is_empty()


def test_one_failed_rerun_does_not_stop_the_others() -> None:
    """Push and PR gates on one SHA are independent; report failure at the end."""
    trigger = _trigger()
    gh = FakeGitHub(
        trigger=trigger,
        gates=[_gate(1), _gate(2), _gate(3)],
        failing_posts=frozenset({1}),
    )

    assert_that(MODULE.refresh(repository=REPO, trigger=trigger, gh=gh)).is_equal_to(1)
    assert_that(gh.reran).is_equal_to([2, 3])


def test_main_rejects_a_malformed_head_sha(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A bad payload fails loudly instead of querying an arbitrary SHA."""
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"workflow_run": _trigger(head_sha="main")}))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))

    assert_that(MODULE.main()).is_equal_to(1)


def _refresh_job() -> dict[str, Any]:
    """Return the ``refresh-gate`` job of the auto-rerun workflow."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["refresh-gate"]
    assert isinstance(job, dict)
    return job


def test_refresh_job_is_scoped_to_same_repo_successful_retries() -> None:
    """Fork runs and first attempts never reach the actions:write token."""
    condition = " ".join(str(_refresh_job()["if"]).split())

    assert_that(condition).contains("github.event.workflow_run.conclusion == 'success'")
    assert_that(condition).contains("github.event.workflow_run.run_attempt > 1")
    assert_that(condition).contains(
        "github.event.workflow_run.head_repository.full_name == github.repository",
    )


def test_refresh_job_runs_trusted_code_with_minimal_permissions() -> None:
    """Only main's copy of the script runs, with just the rerun permission."""
    job = _refresh_job()
    assert_that(job["permissions"]).is_equal_to(
        {"actions": "write", "contents": "read"},
    )
    assert_that(job["concurrency"]["cancel-in-progress"]).is_false()
    assert_that(job["concurrency"]["group"]).contains("workflow_run.head_sha")
    checkout = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    # No ``ref:``: workflow_run checks out the default branch, never the head.
    assert_that(checkout["with"]).does_not_contain_key("ref")
    assert_that(checkout["with"]["persist-credentials"]).is_false()
    sparse = set(checkout["with"]["sparse-checkout"].split())
    assert_that(sparse).is_equal_to(
        {"scripts/ci/refresh-all-checks-gate.py", "scripts/ci/github_api.py"},
    )
    assert_that(job["steps"][-1]["run"]).is_equal_to(
        "python3 scripts/ci/refresh-all-checks-gate.py",
    )


def test_refresh_script_targets_the_real_gate_workflow() -> None:
    """The constants must name the gate file and workflow that actually exist."""
    gate = yaml.safe_load((ROOT / GATE_PATH).read_text(encoding="utf-8"))

    assert_that(MODULE.GATE_PATH).is_equal_to(GATE_PATH)
    assert_that(MODULE.GATE_NAME).is_equal_to(gate["name"])
