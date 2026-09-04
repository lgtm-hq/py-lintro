"""Contract tests for the Renovate tools-image candidate helpers."""

from __future__ import annotations

import importlib.util
import os
import subprocess  # nosec B404 - fixed argv runs the repository script under test
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path) -> ModuleType:
    """Load a standalone CI helper as a test module."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_workflow(name: str) -> dict[Any, Any]:
    """Load a workflow while handling PyYAML's YAML 1.1 ``on`` quirk."""
    path = _REPO_ROOT / ".github" / "workflows" / name
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _trigger(workflow: dict[Any, Any]) -> dict[Any, Any]:
    """Return the parsed workflow trigger mapping."""
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    return trigger


@pytest.fixture
def digest_module() -> ModuleType:
    """Load the Dockerfile digest updater."""
    return _load(
        "update_tools_image_digest",
        _REPO_ROOT / "scripts/ci/update-tools-image-digest.py",
    )


@pytest.fixture
def cleanup_module() -> ModuleType:
    """Load the candidate cleanup helper."""
    return _load(
        "sweep_tools_candidate_tags",
        _REPO_ROOT / "scripts/ci/maintenance/sweep-tools-candidate-tags.py",
    )


@pytest.fixture
def promotion_module() -> ModuleType:
    """Load the main-push promotion classifier."""
    return _load(
        "promote_tools_candidate",
        _REPO_ROOT / "scripts/ci/promote-tools-candidate.py",
    )


@pytest.fixture
def resolver_module() -> ModuleType:
    """Load the Renovate PR resolver."""
    return _load(
        "resolve_renovate_pr",
        _REPO_ROOT / "scripts/ci/resolve-renovate-pr.py",
    )


def test_candidate_workflow_keeps_custom_tag_and_manifest_trigger(
    *,
    promotion_module: ModuleType,
) -> None:
    """The reusable must publish the candidate tag supplied by this workflow."""
    workflow = _load_workflow("docker-tools-candidate.yml")
    trigger = _trigger(workflow)
    push = trigger["push"]
    assert isinstance(push, dict)
    assert "lintro/tools/manifest.src.json" in push["paths"]
    assert set(push["paths"]) == promotion_module.CANDIDATE_PATHS

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    candidate = jobs["candidate-build"]
    assert isinstance(candidate, dict)
    assert candidate["with"]["exact-tags"] is False
    assert "candidate-tag" in candidate["with"]["tags"]


def test_candidate_workflow_preserves_digest_push_security_contract() -> None:
    """Candidate writes stay limited to Renovate and the digest app token."""
    workflow = _load_workflow("docker-tools-candidate.yml")
    trigger = _trigger(workflow)
    push = trigger["push"]
    assert isinstance(push, dict)
    assert_that(push["branches"]).is_equal_to(["renovate/**"])
    assert_that(push["paths"]).contains("lintro/tools/manifest.src.json")

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    resolve = jobs["resolve-pr"]
    candidate = jobs["candidate-build"]
    digest = jobs["push-digest"]
    assert isinstance(resolve, dict)
    assert isinstance(candidate, dict)
    assert isinstance(digest, dict)
    assert_that(resolve["if"]).is_equal_to("github.actor == 'renovate[bot]'")
    assert_that(candidate["if"]).contains("github.actor == 'renovate[bot]'")
    assert_that(candidate["if"]).contains("needs.resolve-pr.result == 'success'")
    assert_that(digest["needs"]).is_equal_to(["resolve-pr", "candidate-build"])
    assert_that(digest["if"]).contains("github.actor == 'renovate[bot]'")
    assert_that(digest["if"]).contains("needs.resolve-pr.result == 'success'")
    assert_that(digest["if"]).contains("needs.candidate-build.result == 'success'")

    checkouts = [
        step
        for job_name in ("resolve-pr", "push-digest")
        for step in jobs[job_name]["steps"]
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert_that(checkouts).is_length(2)
    for checkout in checkouts:
        assert_that(checkout["with"]["persist-credentials"]).is_false()

    steps = digest["steps"]
    mint_index = next(
        index
        for index, step in enumerate(steps)
        if isinstance(step, dict) and step.get("id") == "digest-app"
    )
    push_index = next(
        index
        for index, step in enumerate(steps)
        if isinstance(step, dict) and step.get("name") == "Push digest commit"
    )
    mint = steps[mint_index]
    push_step = steps[push_index]
    assert_that(mint_index).is_equal_to(push_index - 1)
    assert_that(mint["if"]).is_equal_to("steps.update.outputs.changed == 'true'")
    assert_that(mint["uses"]).is_equal_to(
        "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
    )
    assert_that(mint["with"]).is_equal_to(
        {
            "app-id": "${{ secrets.DIGEST_APP_ID }}",
            "private-key": "${{ secrets.DIGEST_APP_PRIVATE_KEY }}",
            "permission-contents": "write",
        },
    )
    assert_that(push_step["if"]).is_equal_to("steps.update.outputs.changed == 'true'")
    digest_token = "${{ steps.digest-app.outputs.token }}"  # nosec B105 - expression
    assert_that(push_step["env"]).is_equal_to(
        {
            "DIGEST_TOKEN": digest_token,
            "REPOSITORY": "${{ github.repository }}",
            "BRANCH": "${{ github.ref_name }}",
        },
    )
    push_script = str(push_step["run"])
    assert_that(push_script).does_not_contain("x-access-token:${DIGEST_TOKEN}@")
    assert_that(push_script).does_not_contain("set-url")
    assert_that(push_script).contains("http.extraheader=AUTHORIZATION: basic")


def test_resolve_pr_retries_until_renovate_creates_pr(
    *,
    resolver_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initial push may race PR creation, so zero results are retried."""
    payloads = iter([[], [{"number": 2243}]])
    sleeps: list[int] = []
    monkeypatch.setattr(resolver_module, "_gh_json", lambda *args: next(payloads))
    monkeypatch.setattr(resolver_module.time, "sleep", sleeps.append)

    assert_that(
        resolver_module.resolve_pr(
            repository="lgtm-hq/py-lintro",
            branch="renovate/tool",
            attempts=3,
            delay_seconds=2,
        ),
    ).is_equal_to(2243)
    assert_that(sleeps).is_equal_to([2])


def test_resolve_pr_exhausted_polling_fails(
    *,
    resolver_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing PR remains an error after the bounded retry window."""
    monkeypatch.setattr(resolver_module, "_gh_json", lambda *args: [])
    monkeypatch.setattr(resolver_module.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="found 0"):
        resolver_module.resolve_pr(
            repository="lgtm-hq/py-lintro",
            branch="renovate/tool",
            attempts=2,
            delay_seconds=0,
        )


def test_candidate_cleanup_checks_out_repository_script() -> None:
    """The maintenance job checks out its committed cleanup helper."""
    workflow = _load_workflow("ghcr-cleanup.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    cleanup = jobs["sweep-tools-candidates"]
    assert isinstance(cleanup, dict)
    steps = cleanup["steps"]
    assert isinstance(steps, list)
    checkout = next(
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert_that(checkout["with"]["persist-credentials"]).is_false()
    sweep = next(
        step
        for step in steps
        if isinstance(step, dict)
        and "scripts/ci/maintenance/sweep-tools-candidate-tags.py"
        in str(step.get("run", ""))
    )
    assert_that(str(sweep["env"]["GH_TOKEN"])).contains("GITHUB_TOKEN")
    assert_that(cleanup["permissions"]["packages"]).is_equal_to("write")
    assert_that(cleanup["permissions"]["pull-requests"]).is_equal_to("read")


def test_candidate_cleanup_age_floor_is_dispatchable() -> None:
    """Operators can override the candidate age floor on manual runs only."""
    workflow = _load_workflow("ghcr-cleanup.yml")
    trigger = _trigger(workflow)
    dispatch = trigger["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    assert_that(dispatch["inputs"]).contains_key("candidate_min_age_days")
    assert_that(dispatch["inputs"]["candidate_min_age_days"]["default"]).is_equal_to(14)

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs["sweep-tools-candidates"]["steps"]
    assert isinstance(steps, list)
    sweep = next(
        step
        for step in steps
        if isinstance(step, dict)
        and "sweep-tools-candidate-tags.py" in str(step.get("run", ""))
    )
    min_age = str(sweep["env"]["MIN_AGE_DAYS"])
    assert_that(min_age).contains("github.event_name != 'workflow_dispatch' && 14")
    assert_that(min_age).contains("inputs.candidate_min_age_days")


def test_candidate_cleanup_runs_from_production_script_path() -> None:
    """The committed production path runs without a package import failure."""
    script = Path("scripts/ci/maintenance/sweep-tools-candidate-tags.py")
    environment = os.environ.copy()
    environment.pop("GH_TOKEN", None)
    environment.pop("GITHUB_TOKEN", None)
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(  # nosec B603 - fixed repository script path
        [sys.executable, str(script)],
        capture_output=True,
        check=False,
        cwd=_REPO_ROOT,
        env=environment,
        text=True,
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("GH_TOKEN is required")
    assert_that(result.stderr).does_not_contain("ModuleNotFoundError")


def test_main_workflow_has_mutually_exclusive_promotion_fallback() -> None:
    """A Renovate merge promotes; ordinary main updates build canonically."""
    workflow = _load_workflow("docker-tools-promote.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    resolve = jobs["resolve"]
    promote = jobs["promote"]
    fallback = jobs["publish-fallback"]
    assert isinstance(resolve, dict)
    assert isinstance(promote, dict)
    assert isinstance(fallback, dict)
    assert "action" in resolve["outputs"]
    trigger = _trigger(workflow)
    assert promote["if"] == (
        "needs.resolve.outputs.action == 'promote' && "
        "github.ref == 'refs/heads/main'"
    )
    assert fallback["if"] == (
        "needs.resolve.outputs.action == 'publish' && "
        "github.ref == 'refs/heads/main'"
    )
    assert "reusable-docker.yml@" in fallback["uses"]
    assert resolve["permissions"]["packages"] == "read"
    assert "workflow_dispatch" not in trigger
    assert resolve["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["concurrency"]["group"] == "lintro-tools-registry"
    cleanup = _load_workflow("ghcr-cleanup.yml")
    assert cleanup["concurrency"]["group"] == workflow["concurrency"]["group"]
    # PR runs validate only and stay out of the shared registry group so a
    # synchronize cannot evict a pending scheduled publish; every event that
    # actually writes to GHCR serializes on lintro-tools-registry.
    publish = _load_workflow("docker-tools-publish.yml")
    assert_that(str(publish["concurrency"]["group"])).contains(
        workflow["concurrency"]["group"],
    )
    assert_that(str(publish["concurrency"]["group"])).contains(
        "github.event_name == 'pull_request'",
    )
    promote_steps = [
        step
        for step in promote["steps"]
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("docker/setup-buildx-action@")
    ]
    assert_that(promote_steps).is_length(1)
    assert_that(promote_steps[0]["with"]["driver"]).is_equal_to("docker")
    assert "docker/tools.Dockerfile" in _trigger(workflow)["push"]["paths"]
    assert (
        ".github/workflows/docker-tools-publish.yml"
        in _trigger(workflow)["push"]["paths"]
    )


def test_digest_updater_changes_both_pin_sites(
    *,
    digest_module: ModuleType,
    tmp_path: Path,
) -> None:
    """The candidate digest is written to both Dockerfiles."""
    old = "a" * 64
    new = "b" * 64
    root = tmp_path / "Dockerfile"
    ai = tmp_path / "ai-tools.Dockerfile"
    root.write_text(
        f"FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:{old} AS tools\n",
        encoding="utf-8",
    )
    ai.write_text(
        f"FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:{old} AS ai-tools\n",
        encoding="utf-8",
    )

    changed = digest_module.update_digest(
        digest=f"sha256:{new}",
        paths=(root, ai),
    )

    assert_that(changed).is_true()
    assert_that(root.read_text(encoding="utf-8")).contains(new)
    assert_that(ai.read_text(encoding="utf-8")).contains(new)
    assert_that(
        digest_module.update_digest(digest=f"sha256:{new}", paths=(root, ai)),
    ).is_false()


def test_digest_updater_rejects_missing_pin_without_partial_write(
    *,
    digest_module: ModuleType,
    tmp_path: Path,
) -> None:
    """A malformed pin site fails before either file is modified."""
    old = "a" * 64
    root = tmp_path / "Dockerfile"
    ai = tmp_path / "ai-tools.Dockerfile"
    root.write_text(
        f"FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:{old} AS tools\n",
        encoding="utf-8",
    )
    ai.write_text("FROM scratch AS ai-tools\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected exactly one"):
        digest_module.update_digest(
            digest=f"sha256:{'b' * 64}",
            paths=(root, ai),
        )
    assert_that(root.read_text(encoding="utf-8")).contains(old)


@pytest.mark.parametrize("suffix", ["0", "g", "-"])
def test_digest_updater_rejects_trailing_digest_character(
    *,
    digest_module: ModuleType,
    suffix: str,
    tmp_path: Path,
) -> None:
    """A digest pin must end at a token boundary after 64 hex characters."""
    digest = "a" * 64
    root = tmp_path / "Dockerfile"
    ai = tmp_path / "ai-tools.Dockerfile"
    root.write_text(
        f"FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:{digest}{suffix} AS tools\n",
        encoding="utf-8",
    )
    ai.write_text(
        f"FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:{digest} AS ai-tools\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected exactly one"):
        digest_module.update_digest(
            digest=f"sha256:{'b' * 64}",
            paths=(root, ai),
        )
    assert_that(root.read_text(encoding="utf-8")).contains(f"sha256:{digest}{suffix}")


def test_cleanup_parser_rejects_versions_with_persistent_tags(
    *,
    cleanup_module: ModuleType,
) -> None:
    """A candidate version carrying a release tag must never be deleted."""
    payload = {
        "id": 7,
        "updated_at": "2026-08-01T00:00:00Z",
        "metadata": {
            "container": {
                "tags": ["tools-candidate-pr42-abcdef1", "latest"],
            },
        },
    }

    assert_that(cleanup_module.candidate_version(payload)).is_none()


def test_cleanup_parser_collects_all_distinct_pr_numbers(
    *,
    cleanup_module: ModuleType,
) -> None:
    """A shared digest retains every candidate PR represented by its tags."""
    payload = {
        "id": 7,
        "updated_at": "2026-08-01T00:00:00Z",
        "metadata": {
            "container": {
                "tags": [
                    "tools-candidate-pr42-abcdef1",
                    "tools-candidate-pr43-fedcba9",
                    "sha-abcdef1",
                    "tools-candidate-pr42-abcdef1",
                ],
            },
        },
    }

    candidate = cleanup_module.candidate_version(payload)

    assert_that(candidate).is_not_none()
    assert_that(candidate.pr_numbers).is_equal_to((42, 43))


def test_cleanup_keeps_recent_shared_digest_with_open_pr(
    *,
    cleanup_module: ModuleType,
) -> None:
    """One open PR protects a recent shared digest from deletion."""
    candidate = cleanup_module.CandidateVersion(
        version_id="7",
        tags=("tools-candidate-pr42-abcdef1", "tools-candidate-pr43-fedcba9"),
        updated_at=datetime.now(UTC) - timedelta(days=1),
        pr_numbers=(42, 43),
    )

    assert_that(
        cleanup_module.should_delete(
            candidate,
            now=datetime.now(UTC),
            pr_states={
                42: ("closed", None),
                43: ("open", None),
            },
            min_age_days=14,
        ),
    ).is_false()


def test_cleanup_deletes_recent_shared_digest_when_all_prs_closed_unmerged(
    *,
    cleanup_module: ModuleType,
) -> None:
    """Every closed-unmerged PR permits deletion before the age limit."""
    candidate = cleanup_module.CandidateVersion(
        version_id="7",
        tags=("tools-candidate-pr42-abcdef1", "tools-candidate-pr43-fedcba9"),
        updated_at=datetime.now(UTC) - timedelta(days=1),
        pr_numbers=(42, 43),
    )

    assert_that(
        cleanup_module.should_delete(
            candidate,
            now=datetime.now(UTC),
            pr_states={
                42: ("closed", None),
                43: ("closed", None),
            },
            min_age_days=14,
        ),
    ).is_true()


def test_cleanup_refresh_skips_version_that_gained_persistent_tag(
    *,
    cleanup_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-delete read protects a candidate promoted during the sweep."""
    candidate = cleanup_module.CandidateVersion(
        version_id="7",
        tags=("tools-candidate-pr42-abcdef1", "sha-abcdef1"),
        updated_at=datetime.now(UTC) - timedelta(days=20),
        pr_number=42,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_gh_json",
        lambda *args: {
            "id": 7,
            "updated_at": "2026-08-01T00:00:00Z",
            "metadata": {
                "container": {
                    "tags": ["tools-candidate-pr42-abcdef1", "latest"],
                },
            },
        },
    )

    assert_that(
        cleanup_module._refresh_candidate(owner="lgtm-hq", candidate=candidate),
    ).is_none()


def test_cleanup_reraises_unrelated_404_text(
    *,
    cleanup_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an HTTP 404 response is treated as a concurrent deletion."""

    def raise_unrelated_error(*args: str) -> object:
        """Raise an error whose digits must not be mistaken for HTTP status."""
        raise RuntimeError("candidate 404 marker")

    monkeypatch.setattr(
        cleanup_module,
        "_gh_json",
        raise_unrelated_error,
    )

    with pytest.raises(RuntimeError, match="candidate 404 marker"):
        cleanup_module._gh_json_allow_not_found("packages/version")


def test_cleanup_main_skips_persistent_tag_added_before_delete(
    *,
    cleanup_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A promotion racing the sweep prevents the destructive delete."""
    old = {
        "id": 7,
        "updated_at": "2026-08-01T00:00:00Z",
        "metadata": {
            "container": {"tags": ["tools-candidate-pr42-abcdef1"]},
        },
    }
    promoted = {
        **old,
        "metadata": {
            "container": {
                "tags": ["tools-candidate-pr42-abcdef1", "latest"],
            },
        },
    }
    payloads = iter([[[old]], {"state": "closed", "merged_at": None}, promoted])
    calls: list[tuple[str, ...]] = []

    def fake_gh_json(*args: str) -> object:
        calls.append(args)
        return next(payloads)

    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setattr(cleanup_module, "_gh_json", fake_gh_json)

    assert_that(cleanup_module.main()).is_equal_to(0)
    assert_that(
        [
            call
            for call in calls
            if "DELETE" in call and any("versions/7" in part for part in call)
        ],
    ).is_empty()


def test_cleanup_main_treats_concurrent_404_as_benign(
    *,
    cleanup_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version deleted by another worker does not fail the sweep."""
    candidate = {
        "id": 7,
        "updated_at": "2026-08-01T00:00:00Z",
        "metadata": {
            "container": {"tags": ["tools-candidate-pr42-abcdef1"]},
        },
    }
    payloads = iter(
        [
            [[candidate]],
            {"state": "closed", "merged_at": None},
            candidate,
            {"state": "closed", "merged_at": None},
        ],
    )
    calls: list[tuple[str, ...]] = []

    def fake_gh_json(*args: str) -> object:
        calls.append(args)
        if "--method" in args and "versions/7" in args[-1]:
            raise RuntimeError("HTTP 404: Not Found")
        return next(payloads)

    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setattr(cleanup_module, "_gh_json", fake_gh_json)

    assert_that(cleanup_module.main()).is_equal_to(0)
    assert_that(
        [
            call
            for call in calls
            if "DELETE" in call and any("versions/7" in part for part in call)
        ],
    ).is_length(1)


def test_cleanup_deletes_closed_unmerged_pr_before_age_limit(
    *,
    cleanup_module: ModuleType,
) -> None:
    """Closed Renovate PRs do not retain candidate storage until expiry."""
    candidate = cleanup_module.CandidateVersion(
        version_id="7",
        tags=("tools-candidate-pr42-abcdef1", "sha-abcdef1"),
        updated_at=datetime.now(UTC) - timedelta(days=1),
        pr_number=42,
    )

    assert_that(
        cleanup_module.should_delete(
            candidate,
            now=datetime.now(UTC),
            pr_state="closed",
            merged_at=None,
            min_age_days=14,
        ),
    ).is_true()


def test_cleanup_keeps_aged_candidate_with_unknown_pr_state(
    *,
    cleanup_module: ModuleType,
) -> None:
    """An unknown owning PR never satisfies the age rule either."""
    candidate = cleanup_module.CandidateVersion(
        version_id="7",
        tags=("tools-candidate-pr42-abcdef1",),
        updated_at=datetime.now(UTC) - timedelta(days=20),
        pr_number=42,
    )

    assert_that(
        cleanup_module.should_delete(
            candidate,
            now=datetime.now(UTC),
            pr_states={42: (None, None)},
            min_age_days=14,
        ),
    ).is_false()
    assert_that(
        cleanup_module.should_delete(
            candidate,
            now=datetime.now(UTC),
            pr_states={},
            min_age_days=14,
        ),
    ).is_false()


def test_cleanup_deletes_aged_candidate_with_known_pr_state(
    *,
    cleanup_module: ModuleType,
) -> None:
    """Age still reaps a candidate once every owning PR is known."""
    candidate = cleanup_module.CandidateVersion(
        version_id="7",
        tags=("tools-candidate-pr42-abcdef1",),
        updated_at=datetime.now(UTC) - timedelta(days=20),
        pr_number=42,
    )

    assert_that(
        cleanup_module.should_delete(
            candidate,
            now=datetime.now(UTC),
            pr_states={42: ("open", None)},
            min_age_days=14,
        ),
    ).is_true()


def test_cleanup_keeps_open_recent_candidate(
    *,
    cleanup_module: ModuleType,
) -> None:
    """Active PR candidates remain available for required-check reruns."""
    candidate = cleanup_module.CandidateVersion(
        version_id="7",
        tags=("tools-candidate-pr42-abcdef1",),
        updated_at=datetime.now(UTC) - timedelta(days=1),
        pr_number=42,
    )

    assert_that(
        cleanup_module.should_delete(
            candidate,
            now=datetime.now(UTC),
            pr_state="open",
            merged_at=None,
            min_age_days=14,
        ),
    ).is_false()


def test_cleanup_protects_recent_candidate_from_merged_pr(
    *,
    cleanup_module: ModuleType,
) -> None:
    """A merged candidate remains until promotion/retention handling completes."""
    candidate = cleanup_module.CandidateVersion(
        version_id="7",
        tags=("tools-candidate-pr42-abcdef1",),
        updated_at=datetime.now(UTC) - timedelta(days=1),
        pr_number=42,
    )

    assert_that(
        cleanup_module.should_delete(
            candidate,
            now=datetime.now(UTC),
            pr_state="closed",
            merged_at="2026-09-01T00:00:00Z",
            min_age_days=14,
        ),
    ).is_false()


def test_promotion_resolves_newest_candidate_for_renovate_merge(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The merged Renovate PR selects its newest candidate tag."""
    payloads = iter(
        [
            [
                {
                    "number": 42,
                    "state": "closed",
                    "merged_at": "2026-09-01T00:00:00Z",
                    "user": {"login": "renovate[bot]"},
                    "head": {"ref": "renovate/rust-1.x"},
                },
            ],
            [
                [{"filename": "lintro/_tool_versions.py"}],
            ],
            [
                [
                    {
                        "updated_at": "2026-09-01T00:00:00Z",
                        "metadata": {
                            "container": {
                                "tags": ["tools-candidate-pr42-abcdef1"],
                            },
                        },
                    },
                    {
                        "updated_at": "2026-09-01T01:00:00Z",
                        "metadata": {
                            "container": {
                                "tags": ["tools-candidate-pr42-fedcba9"],
                            },
                        },
                    },
                ],
            ],
        ],
    )
    monkeypatch.setattr(promotion_module, "_gh_json", lambda *args: next(payloads))

    assert_that(
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="a" * 40,
            ref=promotion_module.MAIN_REF,
        ),
    ).is_equal_to(("promote", "tools-candidate-pr42-fedcba9"))


def test_promotion_resolves_equal_timestamp_by_merged_head(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal registry timestamps select the candidate matching the merged head."""
    payload = [
        {
            "updated_at": "2026-09-01T00:00:00Z",
            "metadata": {
                "container": {
                    "tags": ["tools-candidate-pr42-abcdef1"],
                },
            },
        },
        {
            "updated_at": "2026-09-01T00:00:00Z",
            "metadata": {
                "container": {
                    "tags": ["tools-candidate-pr42-fedcba9"],
                },
            },
        },
    ]
    monkeypatch.setattr(
        promotion_module,
        "_gh_json",
        lambda *args: [payload],
    )

    assert_that(
        promotion_module._candidate_tag_for_pr(
            repository="lgtm-hq/py-lintro",
            pr_number=42,
            head_sha="fedcba9" + "0" * 33,
        ),
    ).is_equal_to("tools-candidate-pr42-fedcba9")


def test_promotion_fails_closed_for_unresolved_equal_timestamp(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal timestamps without a matching head never choose lexicographically."""
    payload = [
        {
            "updated_at": "2026-09-01T00:00:00Z",
            "metadata": {
                "container": {"tags": ["tools-candidate-pr42-abcdef1"]},
            },
        },
        {
            "updated_at": "2026-09-01T00:00:00Z",
            "metadata": {
                "container": {"tags": ["tools-candidate-pr42-fedcba9"]},
            },
        },
    ]
    monkeypatch.setattr(
        promotion_module,
        "_gh_json",
        lambda *args: [payload],
    )

    with pytest.raises(RuntimeError, match="share an updated_at timestamp"):
        promotion_module._candidate_tag_for_pr(
            repository="lgtm-hq/py-lintro",
            pr_number=42,
        )


def test_promotion_falls_back_for_ordinary_main_update(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-Renovate main merge uses the canonical image build fallback."""
    monkeypatch.setattr(
        promotion_module,
        "_gh_json",
        lambda *args: (
            [
                {
                    "number": 43,
                    "state": "closed",
                    "merged_at": "2026-09-01T00:00:00Z",
                    "user": {"login": "maintainer"},
                    "head": {"ref": "fix/tools"},
                },
            ]
            if "/files?" not in args[0]
            else [[{"filename": "scripts/utils/install-tools.sh"}]]
        ),
    )

    assert_that(
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="b" * 40,
            ref=promotion_module.MAIN_REF,
        ),
    ).is_equal_to(("publish", None))


@pytest.mark.parametrize(
    ("author", "branch"),
    [
        ("renovate[bot]", "feature/tool"),
        ("maintainer", "renovate/tool"),
    ],
)
def test_promotion_requires_renovate_author_and_branch(
    *,
    promotion_module: ModuleType,
    author: str,
    branch: str,
) -> None:
    """One-sided Renovate identity signals must not be trusted."""
    assert_that(
        promotion_module._is_renovate_pr(
            {"user": {"login": author}, "head": {"ref": branch}},
        ),
    ).is_false()


def test_promotion_fails_closed_when_renovate_candidate_is_missing(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never rebuild a merged Renovate PR when its candidate was lost."""
    payloads = iter(
        [
            [
                {
                    "number": 44,
                    "state": "closed",
                    "merged_at": "2026-09-01T00:00:00Z",
                    "user": {"login": "renovate[bot]"},
                    "head": {"ref": "renovate/tool"},
                },
            ],
            [[{"filename": "docker/tools.Dockerfile"}]],
            [[]],
        ],
    )
    monkeypatch.setattr(promotion_module, "_gh_json", lambda *args: next(payloads))

    with pytest.raises(RuntimeError, match="refusing a fallback rebuild"):
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="c" * 40,
            ref=promotion_module.MAIN_REF,
        )


def test_promotion_rejects_arbitrary_ref(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The classifier cannot publish or promote from a non-main ref."""
    calls: list[tuple[str, ...]] = []

    def unexpected_github_call(*args: str) -> object:
        calls.append(args)
        raise AssertionError("GitHub must not be queried for an arbitrary ref")

    monkeypatch.setattr(promotion_module, "_gh_json", unexpected_github_call)

    with pytest.raises(RuntimeError, match="requires refs/heads/main"):
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="c" * 40,
            ref="refs/heads/feature",
        )
    assert_that(calls).is_empty()


@pytest.mark.parametrize(
    ("state", "merged_at"),
    [("open", None), ("closed", None)],
)
def test_promotion_rejects_unmerged_renovate_pr(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    merged_at: str | None,
) -> None:
    """An open or closed-unmerged Renovate PR cannot trigger promotion."""
    monkeypatch.setattr(
        promotion_module,
        "_gh_json",
        lambda *args: [
            {
                "number": 48,
                "state": state,
                "merged_at": merged_at,
                "user": {"login": "renovate[bot]"},
                "head": {"ref": "renovate/tool"},
            },
        ],
    )

    with pytest.raises(RuntimeError, match="is not merged"):
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="c" * 40,
            ref=promotion_module.MAIN_REF,
        )


def test_promotion_skips_unrelated_renovate_consumer_digest(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root/AI consumer digest PR does not require a candidate image."""

    def fake_gh_json(*args: str) -> object:
        if "/files?" in args[0]:
            return [
                [
                    {"filename": "Dockerfile"},
                    {"filename": "docker/ai-tools.Dockerfile"},
                ],
            ]
        return [
            {
                "number": 45,
                "state": "closed",
                "merged_at": "2026-09-01T00:00:00Z",
                "user": {"login": "renovate[bot]"},
                "head": {"ref": "renovate/lintro-tools-digest"},
            },
        ]

    monkeypatch.setattr(promotion_module, "_gh_json", fake_gh_json)

    assert_that(
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="d" * 40,
            ref=promotion_module.MAIN_REF,
        ),
    ).is_equal_to(("skip", None))


def test_promotion_publishes_for_renovate_installer_update(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renovate build-input updates use the canonical fallback build."""
    payloads = iter(
        [
            [
                {
                    "number": 46,
                    "state": "closed",
                    "merged_at": "2026-09-01T00:00:00Z",
                    "user": {"login": "renovate[bot]"},
                    "head": {"ref": "renovate/installer"},
                },
            ],
            [[{"filename": "lintro_build/backend.py"}]],
        ],
    )
    monkeypatch.setattr(promotion_module, "_gh_json", lambda *args: next(payloads))

    assert_that(
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="f" * 40,
            ref=promotion_module.MAIN_REF,
        ),
    ).is_equal_to(("publish", None))


def test_promotion_skips_unrelated_maintainer_consumer_digest(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A maintainer consumer-only pin update also needs no image build."""
    payloads = iter(
        [
            [
                {
                    "number": 47,
                    "state": "closed",
                    "merged_at": "2026-09-01T00:00:00Z",
                    "user": {"login": "maintainer"},
                    "head": {"ref": "chore/consumer-pin"},
                },
            ],
            [[{"filename": "Dockerfile"}]],
        ],
    )
    monkeypatch.setattr(promotion_module, "_gh_json", lambda *args: next(payloads))

    assert_that(
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="1" * 40,
            ref=promotion_module.MAIN_REF,
        ),
    ).is_equal_to(("skip", None))


def test_promotion_publishes_for_direct_main_push(
    *,
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A direct main push has no PR and uses the canonical build fallback."""
    monkeypatch.setattr(promotion_module, "_gh_json", lambda *args: [])

    assert_that(
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="e" * 40,
            ref=promotion_module.MAIN_REF,
        ),
    ).is_equal_to(("publish", None))


def test_publish_workflow_keeps_pr_runs_out_of_the_registry_group() -> None:
    """PR validation must not evict a pending scheduled registry publish."""
    workflow = _load_workflow("docker-tools-publish.yml")
    concurrency = workflow["concurrency"]
    assert isinstance(concurrency, dict)
    group = str(concurrency["group"])
    assert_that(group).contains("github.event_name == 'pull_request'")
    assert_that(group).contains("lintro-tools-registry")
    assert_that(str(concurrency["cancel-in-progress"])).contains(
        "github.event_name == 'pull_request'",
    )


def test_sweep_keeps_going_when_one_pull_request_lookup_fails(
    cleanup_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single failing candidate must not strand the rest of the sweep."""
    swept: list[str] = []

    def fake_versions(*, owner: str) -> list[dict[str, Any]]:
        return [
            {
                "id": 1,
                "updated_at": "2020-01-01T00:00:00Z",
                "metadata": {"container": {"tags": ["tools-candidate-pr1-abcdef1"]}},
            },
            {
                "id": 2,
                "updated_at": "2020-01-01T00:00:00Z",
                "metadata": {"container": {"tags": ["tools-candidate-pr2-abcdef2"]}},
            },
        ]

    def fake_sweep(*, candidate: Any, **_: Any) -> None:
        if candidate.version_id == "1":
            raise RuntimeError("gh: HTTP 500")
        swept.append(candidate.version_id)

    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    monkeypatch.setattr(cleanup_module, "_package_versions", fake_versions)
    monkeypatch.setattr(cleanup_module, "_sweep_candidate", fake_sweep)

    exit_code = cleanup_module.main()

    assert_that(exit_code).is_equal_to(1)
    assert_that(swept).is_equal_to(["2"])


def test_missing_pull_request_does_not_raise(
    cleanup_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted PR yields an unknown state instead of aborting the sweep."""

    def fake_gh_json(*args: str) -> object:
        raise RuntimeError("gh: Not Found (HTTP 404)")

    monkeypatch.setattr(cleanup_module, "_gh_json", fake_gh_json)

    assert_that(
        cleanup_module._pull_request(
            repository="lgtm-hq/py-lintro",
            number=7,
        ),
    ).is_equal_to((None, None))


def test_merged_pr_prefers_the_merged_renovate_pull_request(
    promotion_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open companion PR listed first must not decide classification."""
    payload = [
        {
            "number": 10,
            "state": "open",
            "merged_at": None,
            "user": {"login": "someone"},
            "head": {"ref": "feature/x"},
        },
        {
            "number": 11,
            "state": "closed",
            "merged_at": "2026-01-01T00:00:00Z",
            "user": {"login": "renovate[bot]"},
            "head": {"ref": "renovate/tools"},
        },
    ]
    monkeypatch.setattr(promotion_module, "_gh_json", lambda *args: payload)

    resolved = promotion_module._merged_pr(
        repository="lgtm-hq/py-lintro",
        merge_sha="abc123",
    )

    assert resolved is not None
    assert_that(resolved["number"]).is_equal_to(11)
