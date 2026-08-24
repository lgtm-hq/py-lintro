"""Tests for prior-run AI-review state artifact selection (#2158)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "review_state_artifacts.py"


def _load() -> ModuleType:
    """Load the helper as an importable module.

    Returns:
        The loaded module exposing its public helpers.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location("review_state_artifacts", SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["review_state_artifacts"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def artifacts() -> ModuleType:
    """Return the loaded helper module.

    Returns:
        The helper module.
    """
    return _load()


def _ts(*, minutes: int) -> datetime:
    """Return a stable UTC timestamp offset from a fixed origin.

    Args:
        minutes: Minutes after the origin.

    Returns:
        Timezone-aware datetime.
    """
    return datetime(2026, 8, 24, 0, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def _run(
    module: ModuleType,
    run_id: int,
    *,
    minutes: int,
    event: str = "pull_request_target",
    status: str = "completed",
    path: str = ".github/workflows/ai-review.yml",
) -> Any:
    """Build a ``WorkflowRun`` for selection tests.

    Args:
        module: Loaded helper module.
        run_id: Actions run id.
        minutes: Timestamp offset.
        event: Trigger event.
        status: Run status.
        path: Workflow path recorded on the run.

    Returns:
        A ``WorkflowRun`` instance.
    """
    return module.WorkflowRun(
        run_id=run_id,
        event=event,
        status=status,
        path=path,
        created_at=_ts(minutes=minutes),
    )


def _artifact(
    module: ModuleType,
    name: str,
    *,
    expired: bool = False,
) -> Any:
    """Build an ``Artifact`` for selection tests.

    Args:
        module: Loaded helper module.
        name: Artifact name.
        expired: Whether the payload has expired.

    Returns:
        An ``Artifact`` instance.
    """
    return module.Artifact(name=name, expired=expired)


def test_pr_one_prefix_does_not_match_pr_twelve(artifacts: ModuleType) -> None:
    """PR numbers are parsed, not prefix-matched."""
    assert_that(
        artifacts.is_state_artifact_for_pr(
            "lintro-review-state-pr-12-attempt-1-final",
            1,
        ),
    ).is_false()
    assert_that(
        artifacts.is_state_artifact_for_pr(
            "lintro-review-state-pr-12-attempt-1-final",
            12,
        ),
    ).is_true()
    assert_that(
        artifacts.is_state_artifact_for_pr(
            "lintro-review-state-pr-1-attempt-1-final",
            1,
        ),
    ).is_true()


def test_expired_artifact_is_not_valid_state(artifacts: ModuleType) -> None:
    """Expired payloads cannot seed resume."""
    found = artifacts.has_valid_state_artifact(
        [
            _artifact(
                artifacts,
                "lintro-review-state-pr-9-attempt-1-final",
                expired=True,
            ),
        ],
        pr_number=9,
    )
    assert_that(found).is_false()


def test_select_prior_run_id_picks_newest_eligible(artifacts: ModuleType) -> None:
    """Newest completed trusted run with a valid artifact wins."""
    older = _run(artifacts, 11, minutes=1)
    newer = _run(artifacts, 22, minutes=5)
    selected = artifacts.select_prior_run_id(
        [older, newer],
        {
            11: [_artifact(artifacts, "lintro-review-state-pr-7-attempt-1-final")],
            22: [_artifact(artifacts, "lintro-review-state-pr-7-attempt-2-final")],
        },
        pr_number=7,
        current_run_id=99,
    )
    assert_that(selected).is_equal_to(22)


def test_select_prior_run_id_excludes_current_run(artifacts: ModuleType) -> None:
    """The in-progress run is never selected as its own source."""
    current = _run(artifacts, 50, minutes=9)
    prior = _run(artifacts, 40, minutes=3)
    selected = artifacts.select_prior_run_id(
        [current, prior],
        {
            50: [_artifact(artifacts, "lintro-review-state-pr-3-attempt-2-final")],
            40: [_artifact(artifacts, "lintro-review-state-pr-3-attempt-1-final")],
        },
        pr_number=3,
        current_run_id=50,
    )
    assert_that(selected).is_equal_to(40)


def test_select_prior_run_id_ignores_conclusion(artifacts: ModuleType) -> None:
    """Eligibility is artifact presence; conclusion is not a field."""
    failed = _run(artifacts, 8, minutes=4)
    selected = artifacts.select_prior_run_id(
        [failed],
        {8: [_artifact(artifacts, "lintro-review-state-pr-4-attempt-1-part-2")]},
        pr_number=4,
        current_run_id=9,
    )
    assert_that(selected).is_equal_to(8)


def test_select_prior_run_id_rejects_wrong_event(artifacts: ModuleType) -> None:
    """Only ``pull_request_target`` runs are trusted."""
    push_run = _run(artifacts, 6, minutes=2, event="push")
    selected = artifacts.select_prior_run_id(
        [push_run],
        {6: [_artifact(artifacts, "lintro-review-state-pr-5-attempt-1-final")]},
        pr_number=5,
        current_run_id=1,
    )
    assert_that(selected).is_none()


def test_select_prior_run_id_rejects_other_workflow(artifacts: ModuleType) -> None:
    """A state-shaped artifact on another workflow is not trusted."""
    other = _run(
        artifacts,
        6,
        minutes=2,
        path=".github/workflows/test-ci.yml",
    )
    selected = artifacts.select_prior_run_id(
        [other],
        {6: [_artifact(artifacts, "lintro-review-state-pr-5-attempt-1-final")]},
        pr_number=5,
        current_run_id=1,
    )
    assert_that(selected).is_none()


def test_locate_from_env_is_empty_without_repo_or_pr(artifacts: ModuleType) -> None:
    """Missing CI identity degrades to a no-op rather than failing."""
    assert_that(artifacts.locate_from_env({})).is_none()
    assert_that(
        artifacts.locate_from_env({"GITHUB_REPOSITORY": "lgtm-hq/py-lintro"}),
    ).is_none()
    assert_that(artifacts.locate_from_env({"PR_NUMBER": "12"})).is_none()


def test_locate_from_env_uses_injected_api(artifacts: ModuleType) -> None:
    """The locator walks completed runs and their artifacts via ``gh api``."""

    def gh_api(path: str) -> dict[str, Any]:
        if path.startswith("repos/lgtm-hq/py-lintro/actions/workflows/"):
            return {
                "workflow_runs": [
                    {
                        "id": 100,
                        "event": "pull_request_target",
                        "status": "completed",
                        "path": ".github/workflows/ai-review.yml",
                        "created_at": "2026-08-24T01:00:00Z",
                    },
                    {
                        "id": 200,
                        "event": "pull_request_target",
                        "status": "completed",
                        "path": ".github/workflows/ai-review.yml",
                        "created_at": "2026-08-24T02:00:00Z",
                    },
                ],
            }
        if path.startswith("repos/lgtm-hq/py-lintro/actions/runs/200/artifacts"):
            return {
                "artifacts": [
                    {
                        "name": "lintro-review-state-pr-15-attempt-1-final",
                        "expired": False,
                    },
                ],
            }
        return {"artifacts": []}

    selected = artifacts.locate_from_env(
        {
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "PR_NUMBER": "15",
            "GITHUB_RUN_ID": "300",
        },
        gh_api=gh_api,
    )
    assert_that(selected).is_equal_to(200)


def test_write_run_id_appends_github_output(
    artifacts: ModuleType,
    tmp_path: Path,
) -> None:
    """``run-id=`` is appended so other step outputs are preserved."""
    output = tmp_path / "github-output"
    output.write_text("other=1\n", encoding="utf-8")
    artifacts.write_run_id(44, output)
    artifacts.write_run_id(None, output)
    assert_that(output.read_text(encoding="utf-8")).is_equal_to(
        "other=1\nrun-id=44\nrun-id=\n",
    )


def test_locate_from_env_swallows_api_errors(artifacts: ModuleType) -> None:
    """A locator exception must degrade to empty state, never fail the job."""

    def gh_api(_path: str) -> dict[str, Any]:
        raise RuntimeError("github unavailable")

    selected = artifacts.locate_from_env(
        {
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "PR_NUMBER": "15",
            "GITHUB_RUN_ID": "300",
        },
        gh_api=gh_api,
    )
    assert_that(selected).is_none()


def test_main_writes_empty_run_id_without_github(
    artifacts: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI always exits 0 and writes an empty run-id when unused."""
    output = tmp_path / "github-output"
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("PR_NUMBER", raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert_that(artifacts.main(["locate"])).is_equal_to(0)
    assert_that(output.read_text(encoding="utf-8")).is_equal_to("run-id=\n")
