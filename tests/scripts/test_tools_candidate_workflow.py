"""Contract tests for the Renovate tools-image candidate helpers."""

from __future__ import annotations

import importlib.util
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
    assert any(
        isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/checkout@")
        for step in steps
    )


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
    assert promote["if"] == "needs.resolve.outputs.action == 'promote'"
    assert fallback["if"] == "needs.resolve.outputs.action == 'publish'"
    assert "reusable-docker.yml@" in fallback["uses"]
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
    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setattr(cleanup_module, "_gh_json", lambda *args: next(payloads))

    assert_that(cleanup_module.main()).is_equal_to(0)


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
    payloads = iter([[[candidate]], {"state": "closed", "merged_at": None}])

    def fake_gh_json(*args: str) -> object:
        if "versions/7" in args[-1]:
            raise RuntimeError("HTTP 404: Not Found")
        return next(payloads)

    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setattr(cleanup_module, "_gh_json", fake_gh_json)

    assert_that(cleanup_module.main()).is_equal_to(0)


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
        ),
    ).is_equal_to(("promote", "tools-candidate-pr42-fedcba9"))


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
        promotion_module._is_renovate_pr(  # noqa: SLF001
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
                "user": {"login": "renovate[bot]"},
                "head": {"ref": "renovate/lintro-tools-digest"},
            },
        ]

    monkeypatch.setattr(promotion_module, "_gh_json", fake_gh_json)

    assert_that(
        promotion_module.resolve_main_action(
            repository="lgtm-hq/py-lintro",
            merge_sha="d" * 40,
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
        ),
    ).is_equal_to(("publish", None))
