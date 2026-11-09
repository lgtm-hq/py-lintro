"""Keep the dedicated aggregate caller complete and fail-closed."""

from pathlib import Path
from typing import Any, cast

import yaml

from tests.scripts._action_pins import pinned_action_shas

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
GATE = WORKFLOWS / "all-checks-gate.yml"
DEFAULT_PR_TYPES = {"opened", "synchronize", "reopened"}


def _canonical_lgtm_ci_ref() -> str:
    """Return the one lgtm-ci commit every caller pins (#3047).

    The gate adopted the #1197 retry recovery and #1207 block-egress repair
    through its own pin (v0.79.2); since v0.87.0 both ride the canonical pin,
    so the gate must match every other lgtm-ci caller rather than a literal.

    Returns:
        The 40-character lgtm-ci commit SHA shared by all callers.
    """
    shas = {
        sha
        for action, sha in pinned_action_shas().items()
        if action.startswith("lgtm-hq/lgtm-ci/")
    }
    assert len(shas) == 1, f"lgtm-ci callers disagree: {sorted(shas)}"
    return shas.pop()


def _gate() -> dict[str, Any]:
    """Load the caller, including its quoted GitHub event key."""
    return cast("dict[str, Any]", yaml.safe_load(GATE.read_text(encoding="utf-8")))


def test_gate_covers_every_head_pr_activity() -> None:
    """A later title or label check must not leave a stale aggregate pass."""
    required = set(DEFAULT_PR_TYPES)
    for path in WORKFLOWS.glob("*.y*ml"):
        if path == GATE:
            continue
        events = yaml.safe_load(path.read_text(encoding="utf-8")).get("on", {})
        if isinstance(events, dict) and "pull_request" in events:
            trigger = events["pull_request"] or {}
            required.update(trigger.get("types", DEFAULT_PR_TYPES))
    actual = set(_gate()["on"]["pull_request"]["types"])
    assert required <= actual, f"Missing head-PR activities: {required - actual}"


def test_gate_runs_on_main_and_merge_queue() -> None:
    """Adoption needs a real main receipt, not just a historical PR pass."""
    events = _gate()["on"]
    assert events["push"] == {"branches": ["main"]}
    assert "merge_group" in events
    assert not ({"paths", "paths-ignore"} & set(events["pull_request"]))


def test_gate_uses_repaired_released_graph_without_bypasses() -> None:
    """Retry recovery and block egress come from the same reviewed release."""
    workflow = _gate()
    assert workflow["permissions"] == {}
    assert set(workflow["jobs"]) == {"gate"}
    job = workflow["jobs"]["gate"]
    expected = (
        "lgtm-hq/lgtm-ci/.github/workflows/"
        f"reusable-all-checks-gate.yml@{_canonical_lgtm_ci_ref()}"
    )
    assert job["uses"] == expected
    assert job["permissions"] == {
        "actions": "read",
        "checks": "read",
        "contents": "read",
        "statuses": "read",
    }
    # No ignore-checks, allow-empty, audit override, or shortened quiet period.
    assert job["with"] == {"timeout-minutes": 180}
    assert "if" not in job
    assert "secrets" not in job
    assert "continue-on-error" not in job


def test_only_one_caller_file_uses_the_gate() -> None:
    """Two independent gate files would wait on one another."""
    callers = [
        path.name
        for path in WORKFLOWS.glob("*.y*ml")
        if "reusable-all-checks-gate.yml@" in path.read_text(encoding="utf-8")
    ]
    assert callers == [GATE.name]


def test_main_gate_concurrency_is_run_specific() -> None:
    """A following merge must not cancel the previous main gate proof."""
    concurrency = _gate()["concurrency"]
    group = " ".join(concurrency["group"].split())
    assert "github.event.pull_request.number || github.run_id" in group
    assert "'pr' || github.run_attempt" in group
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' }}"
    )


def test_aggregate_retry_requires_operator_reconciliation() -> None:
    """Do not silently grant the aggregate the watcher's three-retry budget."""
    watcher = yaml.safe_load(
        (WORKFLOWS / "auto-rerun-on-infra-failure.yml").read_text(encoding="utf-8"),
    )
    watched = watcher["on"]["workflow_run"]["workflows"]
    assert _gate()["name"] not in watched
