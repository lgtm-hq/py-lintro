"""Contract tests for critical GitHub Actions workflow wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_workflow(*, name: str) -> dict[str, Any]:
    path = _REPO_ROOT / ".github" / "workflows" / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_release_workflows_use_paired_egress_presets() -> None:
    """Auto-tag and version-pr workflows must use opposite egress presets."""
    auto_tag = _load_workflow(name="release-auto-tag.yml")
    version_pr = _load_workflow(name="release-version-pr.yml")

    assert_that(auto_tag["jobs"]["auto-tag"]["with"]["egress-preset"]).is_equal_to(
        "github-tooling",
    )
    assert_that(version_pr["jobs"]["version-pr"]["with"]["egress-preset"]).is_equal_to(
        "pypi",
    )


def test_docker_ci_dogfooding_lint_waits_on_manifest_sync() -> None:
    """Dogfooding lint depends on manifest-sync and allows draft-PR skips."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    job = docker_ci["jobs"]["dogfooding-lint"]
    needs = job["needs"]
    condition = job["if"]

    assert_that(needs).contains("docker-build", "manifest-sync")
    assert_that(condition).contains("!cancelled()")
    assert_that(condition).contains("manifest-sync.result == 'skipped'")
    assert_that(condition).contains("manifest-sync.result == 'success'")
