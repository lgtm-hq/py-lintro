"""Keep the dedicated aggregate caller complete and fail-closed."""

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
GATE = WORKFLOWS / "all-checks-gate.yml"
REPAIRED_GATE_REF = "8f0fd9339df5d30a529aa572ceb51345b8be33a0"
DEFAULT_PR_TYPES = {"opened", "synchronize", "reopened"}


def _gate() -> dict[str, Any]:
    """Load the caller, including its quoted GitHub event key."""
    return yaml.safe_load(GATE.read_text(encoding="utf-8"))


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
        f"reusable-all-checks-gate.yml@{REPAIRED_GATE_REF}"
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


def test_main_gate_concurrency_is_commit_specific() -> None:
    """A following merge must not cancel the previous main gate proof."""
    group = _gate()["concurrency"]["group"]
    assert "github.event_name == 'push' && github.sha" in group
    assert "github.event.pull_request.number || github.ref" in group


def test_aggregate_retry_requires_operator_reconciliation() -> None:
    """Do not silently grant the aggregate the watcher's three-retry budget."""
    watcher = yaml.safe_load(
        (WORKFLOWS / "auto-rerun-on-infra-failure.yml").read_text(encoding="utf-8"),
    )
    watched = watcher["on"]["workflow_run"]["workflows"]
    assert _gate()["name"] not in watched
