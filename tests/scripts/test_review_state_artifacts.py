"""Tests for prior-run AI-review state artifact selection (#2158)."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import sys
import time
import zipfile
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


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


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
    minutes: int | None = None,
    created_at: datetime | None = None,
    event: str = "pull_request_target",
    status: str = "completed",
    path: str = ".github/workflows/ai-review.yml",
    pull_request_numbers: tuple[int, ...] = (),
) -> Any:
    """Build a ``WorkflowRun`` for selection tests.

    Args:
        module: Loaded helper module.
        run_id: Actions run id.
        minutes: Timestamp offset from the origin. Ignored when
            ``created_at`` is set.
        created_at: Exact timestamp. Preferred over ``minutes`` when both
            are provided.
        event: Trigger event.
        status: Run status.
        path: Workflow path recorded on the run.
        pull_request_numbers: PRs attached on the run payload.

    Returns:
        A ``WorkflowRun`` instance.
    """
    timestamp = created_at if created_at is not None else _ts(minutes=minutes or 0)
    return module.WorkflowRun(
        run_id=run_id,
        event=event,
        status=status,
        path=path,
        created_at=timestamp,
        pull_request_numbers=pull_request_numbers,
    )


def _artifact(
    module: ModuleType,
    name: str,
    *,
    expired: bool = False,
    artifact_id: int = 0,
) -> Any:
    """Build an ``Artifact`` for selection tests.

    Args:
        module: Loaded helper module.
        name: Artifact name.
        expired: Whether the payload has expired.
        artifact_id: Actions artifact id; unused unless a seed test needs it.

    Returns:
        An ``Artifact`` instance.
    """
    return module.Artifact(name=name, expired=expired, artifact_id=artifact_id)


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
        now=NOW,
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
        now=NOW,
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
        now=NOW,
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
        now=NOW,
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
        now=NOW,
    )
    assert_that(selected).is_none()


def test_select_prior_run_id_rejects_outside_retention(
    artifacts: ModuleType,
) -> None:
    """A valid artifact older than the retention window is not eligible."""
    stale = _run(
        artifacts,
        6,
        created_at=NOW - timedelta(days=31),
    )
    selected = artifacts.select_prior_run_id(
        [stale],
        {6: [_artifact(artifacts, "lintro-review-state-pr-5-attempt-1-final")]},
        pr_number=5,
        current_run_id=1,
        now=NOW,
    )
    assert_that(selected).is_none()


def test_select_prior_run_id_skips_other_pr(artifacts: ModuleType) -> None:
    """A run known to belong to another PR is not a resume source."""
    other = _run(artifacts, 6, minutes=2, pull_request_numbers=(99,))
    selected = artifacts.select_prior_run_id(
        [other],
        {6: [_artifact(artifacts, "lintro-review-state-pr-5-attempt-1-final")]},
        pr_number=5,
        current_run_id=1,
        now=NOW,
    )
    assert_that(selected).is_none()


def test_select_prior_run_id_checks_artifacts_when_prs_unknown(
    artifacts: ModuleType,
) -> None:
    """Empty ``pull_requests`` still inspects artifact names."""
    unknown = _run(artifacts, 6, minutes=2, pull_request_numbers=())
    selected = artifacts.select_prior_run_id(
        [unknown],
        {6: [_artifact(artifacts, "lintro-review-state-pr-5-attempt-1-final")]},
        pr_number=5,
        current_run_id=1,
        now=NOW,
    )
    assert_that(selected).is_equal_to(6)


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
        now=NOW,
    )
    assert_that(selected).is_equal_to(200)


def test_locate_paginates_past_a_full_first_page(
    artifacts: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid artifact past the first page of runs is still selected."""
    monkeypatch.setattr(artifacts, "RUNS_PER_PAGE", 2)
    requested: list[str] = []

    def _run(run_id: int) -> dict[str, Any]:
        return {
            "id": run_id,
            "event": "pull_request_target",
            "status": "completed",
            "path": ".github/workflows/ai-review.yml",
            "created_at": "2026-08-24T01:00:00Z",
        }

    def gh_api(path: str) -> dict[str, Any]:
        requested.append(path)
        page = 0
        match = re.search(r"[?&]page=(\d+)", path)
        if match is not None:
            page = int(match.group(1))
        if "workflows/" in path and page == 1:
            return {"workflow_runs": [_run(1), _run(2)]}
        if "workflows/" in path and page == 2:
            return {"workflow_runs": [_run(999)]}
        if "runs/999/artifacts" in path:
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
        now=NOW,
    )
    assert_that(selected).is_equal_to(999)
    assert_that(any("page=3" in path for path in requested)).is_false()


def test_fetch_artifacts_unions_pages(
    artifacts: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State on a later artifact page still counts as valid."""
    monkeypatch.setattr(artifacts, "ARTIFACTS_PER_PAGE", 2)

    def gh_api(path: str) -> dict[str, Any]:
        page = 0
        match = re.search(r"[?&]page=(\d+)", path)
        if match is not None:
            page = int(match.group(1))
        if page == 1:
            return {
                "artifacts": [
                    {"name": "other-1", "expired": False},
                    {"name": "other-2", "expired": False},
                ],
            }
        if page == 2:
            return {
                "artifacts": [
                    {
                        "name": "lintro-review-state-pr-15-attempt-1-final",
                        "expired": False,
                    },
                ],
            }
        return {"artifacts": []}

    found = artifacts.fetch_artifacts("lgtm-hq/py-lintro", 9, gh_api=gh_api)
    assert_that(
        artifacts.has_valid_state_artifact(found, pr_number=15),
    ).is_true()


def test_locate_stops_at_retention_cutoff(artifacts: ModuleType) -> None:
    """Runs older than retention end the walk without an artifact fetch."""
    requested: list[str] = []
    stale_created = (NOW - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def gh_api(path: str) -> dict[str, Any]:
        requested.append(path)
        if "workflows/" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 50,
                        "event": "pull_request_target",
                        "status": "completed",
                        "path": ".github/workflows/ai-review.yml",
                        "created_at": stale_created,
                    },
                ],
            }
        return {"artifacts": [{"name": "should-not-fetch", "expired": False}]}

    selected = artifacts.locate_from_env(
        {
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "PR_NUMBER": "15",
            "GITHUB_RUN_ID": "300",
        },
        gh_api=gh_api,
        now=NOW,
    )
    assert_that(selected).is_none()
    assert_that(any("/artifacts" in path for path in requested)).is_false()


def test_locate_skips_artifact_fetch_for_other_pr(artifacts: ModuleType) -> None:
    """A run that lists another PR is skipped without fetching artifacts."""
    requested: list[str] = []

    def gh_api(path: str) -> dict[str, Any]:
        requested.append(path)
        if "workflows/" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 50,
                        "event": "pull_request_target",
                        "status": "completed",
                        "path": ".github/workflows/ai-review.yml",
                        "created_at": "2026-08-24T01:00:00Z",
                        "pull_requests": [{"number": 99}],
                    },
                ],
            }
        return {
            "artifacts": [
                {
                    "name": "lintro-review-state-pr-15-attempt-1-final",
                    "expired": False,
                },
            ],
        }

    selected = artifacts.locate_from_env(
        {
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "PR_NUMBER": "15",
            "GITHUB_RUN_ID": "300",
        },
        gh_api=gh_api,
        now=NOW,
    )
    assert_that(selected).is_none()
    assert_that(any("/artifacts" in path for path in requested)).is_false()


def test_locate_checks_artifacts_when_pull_requests_empty(
    artifacts: ModuleType,
) -> None:
    """``pull_request_target`` often omits ``pull_requests``; names decide."""
    requested: list[str] = []

    def gh_api(path: str) -> dict[str, Any]:
        requested.append(path)
        if "workflows/" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 50,
                        "event": "pull_request_target",
                        "status": "completed",
                        "path": ".github/workflows/ai-review.yml",
                        "created_at": "2026-08-24T01:00:00Z",
                        "pull_requests": [],
                    },
                ],
            }
        return {
            "artifacts": [
                {
                    "name": "lintro-review-state-pr-15-attempt-1-final",
                    "expired": False,
                },
            ],
        }

    selected = artifacts.locate_from_env(
        {
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "PR_NUMBER": "15",
            "GITHUB_RUN_ID": "300",
        },
        gh_api=gh_api,
        now=NOW,
    )
    assert_that(selected).is_equal_to(50)
    assert_that(any("/artifacts" in path for path in requested)).is_true()


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


def test_locate_retries_a_failed_artifact_page(
    artifacts: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient artifact-list failure must not hide a valid persist."""
    monkeypatch.setattr(artifacts, "_retry_sleep", lambda _seconds: None)
    attempts = {"artifacts": 0}

    def gh_api(path: str) -> dict[str, Any] | None:
        if "workflows/" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 200,
                        "event": "pull_request_target",
                        "status": "completed",
                        "path": ".github/workflows/ai-review.yml",
                        "created_at": "2026-08-24T02:00:00Z",
                    },
                ],
            }
        if "runs/200/artifacts" in path:
            attempts["artifacts"] += 1
            if attempts["artifacts"] < 3:
                return None
            return {
                "artifacts": [
                    {
                        "id": 9,
                        "name": "lintro-review-state-pr-15-attempt-1-inline",
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
        now=NOW,
    )
    assert_that(selected).is_equal_to(200)
    assert_that(attempts["artifacts"]).is_equal_to(3)


def test_locate_skips_run_with_missing_created_at(artifacts: ModuleType) -> None:
    """An unusable timestamp must not abort the newest-first walk."""

    def gh_api(path: str) -> dict[str, Any]:
        if "workflows/" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 50,
                        "event": "pull_request_target",
                        "status": "completed",
                        "path": ".github/workflows/ai-review.yml",
                        "created_at": "",
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
        if "runs/200/artifacts" in path:
            return {
                "artifacts": [
                    {
                        "name": "lintro-review-state-pr-15-attempt-1-inline",
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
        now=NOW,
    )
    assert_that(selected).is_equal_to(200)


def test_locate_prior_state_keeps_newest_and_older_seed(
    artifacts: ModuleType,
) -> None:
    """Newest-wins stays; the previous same-PR persist is the seed."""

    def _run_payload(run_id: int, created: str) -> dict[str, Any]:
        return {
            "id": run_id,
            "event": "pull_request_target",
            "status": "completed",
            "path": ".github/workflows/ai-review.yml",
            "created_at": created,
            "pull_requests": [{"number": 15}],
        }

    def gh_api(path: str) -> dict[str, Any]:
        if "workflows/" in path:
            return {
                "workflow_runs": [
                    _run_payload(300, "2026-08-24T03:00:00Z"),
                    _run_payload(200, "2026-08-24T02:00:00Z"),
                    _run_payload(100, "2026-08-24T01:00:00Z"),
                ],
            }
        if "runs/300/artifacts" in path or "runs/200/artifacts" in path:
            return {
                "artifacts": [
                    {
                        "id": 1,
                        "name": "lintro-review-state-pr-15-attempt-1-inline",
                        "expired": False,
                    },
                ],
            }
        return {"artifacts": []}

    located = artifacts.locate_state_from_env(
        {
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "PR_NUMBER": "15",
            "GITHUB_RUN_ID": "400",
        },
        gh_api=gh_api,
        now=NOW,
    )
    assert_that(located.run_id).is_equal_to(300)
    assert_that(located.seed_run_id).is_equal_to(200)


def test_parse_artifact_keeps_numeric_id(artifacts: ModuleType) -> None:
    """Seed downloads need the Actions artifact id."""
    parsed = artifacts.parse_artifact(
        {
            "id": 9556725323,
            "name": "lintro-review-state-pr-2166-attempt-1-inline",
            "expired": False,
        },
    )
    assert_that(parsed).is_not_none()
    assert_that(parsed.artifact_id).is_equal_to(9556725323)


def test_extract_seed_parts_rewrites_names(
    artifacts: ModuleType,
    tmp_path: Path,
) -> None:
    """Older parts sort before ``part-0001.json`` so newest findings win."""
    payload = {"coverage": [{"path": "a.py", "hash": "abc"}], "pr_number": 15}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("part-0002.json", json.dumps(payload))
        bundle.writestr("state.json", json.dumps(payload))
        bundle.writestr("notes.txt", "ignore")
    written = artifacts.extract_seed_parts(
        buffer.getvalue(),
        run_id=32829243399,
        directory=tmp_path,
    )
    assert_that(written).is_equal_to(2)
    names = sorted(path.name for path in tmp_path.iterdir())
    assert_that(names).is_equal_to(
        [
            "part-0000-prior-32829243399-part-0002.json",
            "part-0000-prior-32829243399-state.json",
        ],
    )


def test_seed_prior_run_state_prefers_inline(
    artifacts: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final inline persist is the seed source when several exist."""
    monkeypatch.setattr(artifacts, "_retry_sleep", lambda _seconds: None)
    payload = {"coverage": [{"path": "a.py", "hash": "abc"}], "pr_number": 15}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("part-0001.json", json.dumps(payload))
    archive = buffer.getvalue()
    requested: list[str] = []

    def gh_api(path: str) -> dict[str, Any]:
        requested.append(path)
        return {
            "artifacts": [
                {
                    "id": 11,
                    "name": "lintro-review-state-pr-15-attempt-1-ckpt-100",
                    "expired": False,
                },
                {
                    "id": 22,
                    "name": "lintro-review-state-pr-15-attempt-1-inline",
                    "expired": False,
                },
            ],
        }

    def gh_api_bytes(path: str) -> bytes | None:
        requested.append(path)
        if path.endswith("/22/zip"):
            return archive
        return None

    written = artifacts.seed_prior_run_state(
        repo="lgtm-hq/py-lintro",
        pr_number=15,
        seed_run_id=200,
        directory=tmp_path,
        gh_api=gh_api,
        gh_api_bytes=gh_api_bytes,
    )
    assert_that(written).is_equal_to(1)
    assert_that(any(path.endswith("/22/zip") for path in requested)).is_true()
    assert_that(any(path.endswith("/11/zip") for path in requested)).is_false()
    assert_that((tmp_path / "part-0000-prior-200-part-0001.json").is_file()).is_true()


def test_pick_seed_artifact_ignores_expired_and_idless(
    artifacts: ModuleType,
) -> None:
    """A seed artifact must be downloadable and owned by this PR."""
    chosen = artifacts.pick_seed_artifact(
        [
            _artifact(
                artifacts,
                "lintro-review-state-pr-15-attempt-1-inline",
                expired=True,
                artifact_id=1,
            ),
            _artifact(
                artifacts,
                "lintro-review-state-pr-15-attempt-1-inline",
                artifact_id=0,
            ),
            _artifact(
                artifacts,
                "lintro-review-state-pr-99-attempt-1-inline",
                artifact_id=3,
            ),
            _artifact(
                artifacts,
                "lintro-review-state-pr-15-attempt-1-inline",
                artifact_id=4,
            ),
        ],
        pr_number=15,
    )
    assert_that(chosen).is_not_none()
    assert_that(chosen.artifact_id).is_equal_to(4)


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


def _runtime_jwt() -> str:
    """Build a runtime JWT whose scp claim carries Results backend IDs.

    Returns:
        Three-part JWT string.
    """
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    payload = (
        base64.urlsafe_b64encode(
            b'{"scp":"Actions.Example Actions.Results:run-backend:job-backend"}',
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{header}.{payload}.sig"


def test_state_artifact_name_keeps_the_pr_prefix(artifacts: ModuleType) -> None:
    """In-step uploads must still match the locator/download prefix."""
    name = artifacts.state_artifact_name(pr_number=2166, attempt=2, suffix="inline")
    assert_that(name).is_equal_to("lintro-review-state-pr-2166-attempt-2-inline")
    assert_that(artifacts.is_state_artifact_for_pr(name, 2166)).is_true()
    dirty = artifacts.state_artifact_name(
        pr_number=2166,
        attempt=1,
        suffix="ckpt 15/../x",
    )
    assert_that(dirty).is_equal_to("lintro-review-state-pr-2166-attempt-1-ckpt-15-x")


def test_upload_state_creates_puts_and_finalizes(
    artifacts: ModuleType,
    tmp_path: Path,
) -> None:
    """The in-step uploader walks CreateArtifact, blob PUT, FinalizeArtifact."""
    state_dir = tmp_path / "ai-review-state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text('{"schema_version": 3}\n', encoding="utf-8")
    (state_dir / "part-0001.json").write_text(
        '{"schema_version": 3, "coverage": []}\n',
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    def http_do(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        calls.append((method, url))
        if url.endswith("/CreateArtifact"):
            assert_that(headers["Authorization"]).starts_with("Bearer ")
            payload = json.loads(body.decode("utf-8"))
            assert_that(payload["name"]).contains("lintro-review-state-pr-2166-")
            assert_that(payload["workflowRunBackendId"]).is_equal_to("run-backend")
            assert_that(payload["mimeType"]).is_equal_to("application/zip")
            return (
                200,
                b'{"ok":true,"signedUploadUrl":"https://blob.example/upload"}',
            )
        if url == "https://blob.example/upload":
            assert_that(method).is_equal_to("PUT")
            assert_that(headers["x-ms-blob-type"]).is_equal_to("BlockBlob")
            assert_that(headers["x-ms-version"]).is_equal_to("2023-11-03")
            assert_that(body).contains(b"part-0001.json")
            assert_that(body).does_not_contain(b"state.json")
            return 201, b""
        if url.endswith("/FinalizeArtifact"):
            payload = json.loads(body.decode("utf-8"))
            assert_that(payload["size"]).is_not_equal_to("0")
            assert_that(payload["hash"]).starts_with("sha256:")
            return 200, b'{"ok":true,"artifactId":"9"}'
        return 500, b"unexpected"

    uploaded = artifacts.upload_from_env(
        {
            "ACTIONS_RUNTIME_TOKEN": _runtime_jwt(),
            "ACTIONS_RESULTS_URL": "https://results-receiver.actions.githubusercontent.com/",
            "PR_NUMBER": "2166",
            "GITHUB_RUN_ATTEMPT": "3",
            "LINTRO_REVIEW_STATE_DIR": str(state_dir),
        },
        suffix="inline",
        http_do=http_do,
    )
    assert_that(uploaded).is_true()
    assert_that(calls).is_length(3)
    assert_that(calls[0][1]).contains("CreateArtifact")
    assert_that(calls[1][0]).is_equal_to("PUT")
    assert_that(calls[2][1]).contains("FinalizeArtifact")


def test_upload_from_env_is_fail_safe_without_runtime_token(
    artifacts: ModuleType,
    tmp_path: Path,
) -> None:
    """Local or pre-token runs must no-op instead of failing the review."""
    state_dir = tmp_path / "ai-review-state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text("{}", encoding="utf-8")
    uploaded = artifacts.upload_from_env(
        {
            "PR_NUMBER": "2166",
            "LINTRO_REVIEW_STATE_DIR": str(state_dir),
        },
        suffix="inline",
    )
    assert_that(uploaded).is_false()


def test_upload_rejects_untrusted_results_host(
    artifacts: ModuleType,
    tmp_path: Path,
) -> None:
    """The Twirp client must not follow an arbitrary RESULTS_URL."""
    state_dir = tmp_path / "ai-review-state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text("{}", encoding="utf-8")

    calls: list[tuple[object, ...]] = []

    def http_do(*args: object) -> tuple[int, bytes]:
        calls.append(args)
        return 200, b"{}"

    uploaded = artifacts.upload_from_env(
        {
            "ACTIONS_RUNTIME_TOKEN": _runtime_jwt(),
            "ACTIONS_RESULTS_URL": "https://evil.example/",
            "PR_NUMBER": "2166",
            "LINTRO_REVIEW_STATE_DIR": str(state_dir),
        },
        suffix="inline",
        http_do=http_do,
    )
    assert_that(uploaded).is_false()
    assert_that(calls).is_empty()


def _state_env(
    tmp_path: Path,
    *,
    with_part: bool = True,
) -> tuple[Path, dict[str, str]]:
    """Create a state directory and the Actions env the uploader reads.

    Args:
        tmp_path: Pytest temp directory.
        with_part: When True, include a ``part-*.json`` file.

    Returns:
        State directory and environment mapping.
    """
    state_dir = tmp_path / "ai-review-state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text('{"schema_version": 3}\n', encoding="utf-8")
    if with_part:
        (state_dir / "part-0001.json").write_text(
            '{"schema_version": 3, "coverage": []}\n',
            encoding="utf-8",
        )
    return state_dir, {
        "ACTIONS_RUNTIME_TOKEN": _runtime_jwt(),
        "ACTIONS_RESULTS_URL": "https://results-receiver.actions.githubusercontent.com/",
        "PR_NUMBER": "2166",
        "GITHUB_RUN_ATTEMPT": "3",
        "LINTRO_REVIEW_STATE_DIR": str(state_dir),
    }


def test_main_upload_never_exits_nonzero(
    artifacts: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upload CLI is fail-safe even when nothing can be uploaded."""
    monkeypatch.delenv("ACTIONS_RUNTIME_TOKEN", raising=False)
    monkeypatch.delenv("ACTIONS_RESULTS_URL", raising=False)
    monkeypatch.setenv("PR_NUMBER", "2166")
    assert_that(artifacts.main(["upload", "--suffix", "inline"])).is_equal_to(0)


def test_upload_budget_zero_makes_no_http_calls(
    artifacts: ModuleType,
    tmp_path: Path,
) -> None:
    """A spent budget must not start Create/PUT/Finalize."""
    _state_dir, env = _state_env(tmp_path)
    calls: list[str] = []

    def http_do(
        method: str,
        url: str,
        _headers: dict[str, str],
        _body: bytes,
    ) -> tuple[int, bytes]:
        calls.append(f"{method} {url}")
        return 200, b'{"ok":true}'

    uploaded = artifacts.upload_from_env(
        env,
        suffix="inline",
        http_do=http_do,
        budget_seconds=0,
    )
    assert_that(uploaded).is_false()
    assert_that(calls).is_empty()


def test_upload_budget_stops_after_the_first_slow_request(
    artifacts: ModuleType,
    tmp_path: Path,
) -> None:
    """A request that consumes the budget must skip PUT and Finalize."""
    _state_dir, env = _state_env(tmp_path)
    calls: list[str] = []

    def http_do(
        method: str,
        url: str,
        _headers: dict[str, str],
        _body: bytes,
    ) -> tuple[int, bytes]:
        calls.append(url.rsplit("/", 1)[-1] if method != "PUT" else "PUT")
        time.sleep(0.05)
        if url.endswith("/CreateArtifact"):
            return 200, b'{"ok":true,"signedUploadUrl":"https://blob.example/upload"}'
        return 200, b'{"ok":true}'

    uploaded = artifacts.upload_from_env(
        env,
        suffix="inline",
        http_do=http_do,
        budget_seconds=0.02,
    )
    assert_that(uploaded).is_false()
    assert_that(calls).is_equal_to(["CreateArtifact"])


def test_upload_from_env_fails_closed_on_http_errors(
    artifacts: ModuleType,
    tmp_path: Path,
) -> None:
    """Create 500, ok:false, PUT 4xx, and omitted Finalize ok are not success."""
    _state_dir, env = _state_env(tmp_path)

    def create_500(
        _method: str,
        _url: str,
        _headers: dict[str, str],
        _body: bytes,
    ) -> tuple[int, bytes]:
        return 500, b"nope"

    def ok_false(
        _method: str,
        url: str,
        _headers: dict[str, str],
        _body: bytes,
    ) -> tuple[int, bytes]:
        if url.endswith("/CreateArtifact"):
            return 200, b'{"ok":false}'
        return 200, b"{}"

    def put_403(
        _method: str,
        url: str,
        _headers: dict[str, str],
        _body: bytes,
    ) -> tuple[int, bytes]:
        if url.endswith("/CreateArtifact"):
            return 200, b'{"ok":true,"signedUploadUrl":"https://blob.example/upload"}'
        if url == "https://blob.example/upload":
            return 403, b"blocked"
        return 200, b'{"ok":true}'

    def finalize_omits_ok(
        _method: str,
        url: str,
        _headers: dict[str, str],
        _body: bytes,
    ) -> tuple[int, bytes]:
        if url.endswith("/CreateArtifact"):
            return 200, b'{"ok":true,"signedUploadUrl":"https://blob.example/upload"}'
        if url == "https://blob.example/upload":
            return 201, b""
        return 200, b'{"artifactId":"9"}'

    for http_do in (create_500, ok_false, put_403, finalize_omits_ok):
        assert_that(
            artifacts.upload_from_env(env, suffix="inline", http_do=http_do),
        ).is_false()


def test_inline_suffix_uses_the_cancel_budget_by_default(
    artifacts: ModuleType,
) -> None:
    """Post-wait uploads default to 2s so classify still fits in SIGTERM grace."""
    assert_that(artifacts._CANCEL_UPLOAD_BUDGET_SECONDS).is_equal_to(2.0)
    assert_that(artifacts._CANCEL_UPLOAD_BUDGET_SECONDS).is_less_than(
        artifacts._UPLOAD_TIMEOUT_SECONDS,
    )
    assert_that(artifacts._CHECKPOINT_UPLOAD_BUDGET_SECONDS).is_greater_than(
        artifacts._CANCEL_UPLOAD_BUDGET_SECONDS,
    )
