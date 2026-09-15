"""Scenario invariants over the recorded release validation runs (#2633).

``docs/release-validation.md`` drives four ``rc`` tags through the tag pipeline:
S1 green, S2 forced build failure, S3 forced npm publish failure, S4 the recovery
dry run against S3, refused by the prerelease exemption. Each run is recorded as one JSON fixture under
``tests/fixtures/release-validation/`` (schema in that directory's README). This
module asserts the runbook's invariants over whatever fixtures exist, so a
future pipeline change that alters the shape breaks a test here rather than a
release. A scenario with no fixture yet skips with a reason: the scenario PR
only adds JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "release-validation"
_TAG_PIPELINE = _REPO_ROOT / ".github" / "workflows" / "publish-pypi-on-tag.yml"

_SCENARIOS = ("S1", "S2", "S3", "S4")
_RC_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)rc(\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONCLUSIONS = {"success", "failure", "skipped", "cancelled"}

_REQUIRED_KEYS = {
    "scenario",
    "tag",
    "version",
    "npm_version",
    "run",
    "switches",
    "jobs",
    "published",
    "issues",
    "wall_clock_seconds",
    "recovery",
    "notes",
}
_OPTIONAL_KEYS = {"verification", "attempts"}
_RUN_KEYS = {"id", "url", "attempt", "conclusion"}
_ATTEMPT_KEYS = {"run_id", "attempt", "conclusion", "note"}
_PUBLISHED_KEYS = {"pypi", "github_release", "docker", "npm", "homebrew"}
_ISSUE_KEYS = {"release_failure", "closing_comment"}
_RELEASE_FAILURE_KEYS = {"url", "state"}
_RELEASE_FAILURE_OPTIONAL_KEYS = {"closed_by", "closed_at"}
_RECOVERY_KEYS = {"source_run_id", "dry_run", "live"}
_DRY_RUN_KEYS = {"id", "url", "conclusion", "channels"}
_DRY_RUN_REFUSAL_KEYS = {"refusal", "policy"}
#: The rule S4 records when the recovery refuses a prerelease (lgtm-ci#962).
_PRERELEASE_EXEMPTION = (
    "lgtm-ci#962 prerelease exemption (docs/release-recovery.md, Which tier applies)"
)

_RELEASE_IMAGES = (
    "ghcr.io/lgtm-hq/py-lintro-base",
    "ghcr.io/lgtm-hq/py-lintro",
    "ghcr.io/lgtm-hq/py-lintro-ai",
)
#: npm package -> the release asset whose bytes it wraps (``None`` for the
#: meta package, which carries no binary).
_NPM_PACKAGES: dict[str, str | None] = {
    "@lgtm-hq/lintro": None,
    "@lgtm-hq/lintro-darwin-arm64": "lintro-macos-arm64",
    "@lgtm-hq/lintro-linux-arm64": "lintro-linux-arm64",
    "@lgtm-hq/lintro-linux-x64": "lintro-linux-x64",
}
#: Jobs that write to a channel, in the order the runbook checks them.
_PUBLISH_JOBS = ("pypi-upload", "github-release", "docker-promote", "npm-publish")
#: Jobs an rc always skips, validation channels or not.
_STABLE_ONLY_JOBS = ("homebrew-tap", "mirror-token", "mirror-release")
_BUILD_JOBS = ("sbom", "pypi-build", "build-binaries", "docker-build")

_EXPECTED_SWITCHES = {
    "S1": {"RELEASE_VALIDATION_CHANNELS": "true", "RELEASE_FAULT": ""},
    "S2": {"RELEASE_VALIDATION_CHANNELS": "true", "RELEASE_FAULT": "fail-build"},
    "S3": {"RELEASE_VALIDATION_CHANNELS": "true", "RELEASE_FAULT": "fail-publish-npm"},
    "S4": {"RELEASE_VALIDATION_CHANNELS": "true", "RELEASE_FAULT": ""},
}


def _load_fixtures() -> dict[str, dict[str, Any]]:
    """Load every scenario fixture present, keyed by scenario id.

    Returns:
        Scenario id to parsed JSON document; empty when none is recorded yet.
    """
    fixtures: dict[str, dict[str, Any]] = {}
    if not _FIXTURE_DIR.is_dir():
        return fixtures
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert_that(data).described_as(path.name).is_instance_of(dict)
        fixtures[path.stem] = cast(dict[str, Any], data)
    return fixtures


_FIXTURES = _load_fixtures()


def _fixture(scenario: str) -> dict[str, Any]:
    """Return the fixture for ``scenario`` or skip the calling test.

    Args:
        scenario: One of ``S1``..``S4``.

    Returns:
        The parsed fixture.
    """
    if scenario not in _FIXTURES:
        pytest.skip(
            f"{scenario} has not been recorded yet: add "
            f"tests/fixtures/release-validation/{scenario}.json once the "
            "runbook scenario has run (#2633)",
        )
    return _FIXTURES[scenario]


def _tag_pipeline_job_ids() -> set[str]:
    """Return every job id of the tag pipeline, the rows a fixture must carry."""
    data = yaml.safe_load(_TAG_PIPELINE.read_text(encoding="utf-8"))
    return set(cast(dict[str, Any], data["jobs"]))


def _is_empty(channel: Any) -> bool:
    """Return whether a ``published`` channel records nothing."""
    return channel is None or channel == {}


def _assert_channels_present(published: dict[str, Any], *, version: str) -> None:
    """Assert PyPI, the GitHub Release and the three images are all recorded.

    Args:
        published: The fixture's ``published`` block.
        version: The PEP 440 version the file names must carry.
    """
    pypi = published["pypi"]
    assert_that(pypi).is_instance_of(dict)
    assert_that(set(pypi)).is_equal_to(
        {f"lintro-{version}.tar.gz", f"lintro-{version}-py3-none-any.whl"},
    )
    for name, digest in pypi.items():
        assert_that(digest).described_as(name).matches(_SHA256_RE.pattern)
    release = published["github_release"]
    assert_that(release["prerelease"]).is_true()
    assets = release["assets"]
    required_assets = {
        *pypi,
        "lintro-macos-arm64",
        "lintro-linux-arm64",
        "lintro-linux-x64",
        "lintro.1",
    }
    assert_that(required_assets).is_subset_of(set(assets))
    for name, digest in assets.items():
        assert_that(digest).described_as(name).matches(_SHA256_RE.pattern)
    # The release carries the exact dist files PyPI has.
    for name, digest in pypi.items():
        assert_that(assets[name]).described_as(name).is_equal_to(digest)
    docker = published["docker"]
    assert_that(set(docker)).is_equal_to(set(_RELEASE_IMAGES))
    for image, digest in docker.items():
        assert_that(digest).described_as(image).matches(_IMAGE_DIGEST_RE.pattern)


def _assert_npm_present(
    published: dict[str, Any],
    *,
    npm_version: str,
    assets: dict[str, str],
) -> None:
    """Assert all four packages are under ``next`` and wrap the release binaries.

    Args:
        published: The fixture's ``published`` block.
        npm_version: The SemVer form every package must be at.
        assets: The release assets (name to sha256) the binaries must equal.
    """
    npm = published["npm"]
    assert_that(set(npm)).is_equal_to(set(_NPM_PACKAGES))
    for package, asset in _NPM_PACKAGES.items():
        record = npm[package]
        assert_that(record["version"]).described_as(package).is_equal_to(npm_version)
        assert_that(record["dist_tag"]).described_as(package).is_equal_to("next")
        if asset is None:
            continue
        assert_that(record["binary_sha256"]).described_as(package).is_equal_to(
            assets[asset],
        )


# --- schema -------------------------------------------------------------------


def test_fixture_directory_documents_the_schema() -> None:
    """The README is the schema; it must exist even before any run is recorded."""
    readme = _FIXTURE_DIR / "README.md"
    assert_that(readme.exists()).is_true()
    text = readme.read_text(encoding="utf-8")
    for key in sorted(_REQUIRED_KEYS):
        assert_that(text).described_as(key).contains(f'"{key}"')


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_fixture_follows_the_schema(scenario: str) -> None:
    """Every recorded scenario carries exactly the documented keys and shapes."""
    fixture = _fixture(scenario)
    keys = set(fixture)
    assert_that(keys - _OPTIONAL_KEYS).is_equal_to(_REQUIRED_KEYS)
    assert_that(fixture["scenario"]).is_equal_to(scenario)
    assert_that(fixture["tag"]).matches(_RC_TAG_RE.pattern)
    match = _RC_TAG_RE.match(fixture["tag"])
    assert match is not None
    core, number = match.groups()
    assert_that(fixture["version"]).is_equal_to(f"{core}rc{number}")
    assert_that(fixture["npm_version"]).is_equal_to(f"{core}-rc.{number}")
    run = fixture["run"]
    assert_that(set(run)).is_equal_to(_RUN_KEYS)
    assert_that(run["id"]).is_instance_of(int)
    assert_that(run["url"]).ends_with(f"/actions/runs/{run['id']}")
    assert_that(run["attempt"]).is_instance_of(int)
    assert_that(run["conclusion"]).is_in(*_CONCLUSIONS)
    assert_that(fixture["switches"]).is_equal_to(_EXPECTED_SWITCHES[scenario])
    jobs = fixture["jobs"]
    assert_that(set(jobs)).is_equal_to(_tag_pipeline_job_ids())
    for job_id, conclusion in jobs.items():
        assert_that(conclusion).described_as(job_id).is_in(*_CONCLUSIONS)
    assert_that(set(fixture["published"])).is_equal_to(_PUBLISHED_KEYS)
    assert_that(fixture["published"]["homebrew"]).is_none()
    assert_that(set(fixture["issues"])).is_equal_to(_ISSUE_KEYS)
    issue = fixture["issues"]["release_failure"]
    if issue is not None:
        assert_that(set(issue) - _RELEASE_FAILURE_OPTIONAL_KEYS).is_equal_to(
            _RELEASE_FAILURE_KEYS,
        )
        assert_that(issue["state"]).is_in("open", "closed")
    for attempt in fixture.get("attempts", []):
        assert_that(set(attempt)).is_equal_to(_ATTEMPT_KEYS)
        assert_that(attempt["run_id"]).is_instance_of(int)
        assert_that(attempt["attempt"]).is_instance_of(int)
        assert_that(attempt["conclusion"]).is_in(*_CONCLUSIONS)
        assert_that(attempt["note"].strip()).is_not_empty()
    assert_that(fixture["notes"]).is_instance_of(str)


def test_recorded_scenarios_share_one_version_and_distinct_runs() -> None:
    """All four candidates are one X.Y.Z, in order, from different runs."""
    if not _FIXTURES:
        pytest.skip("no release validation scenario recorded yet (#2633)")
    cores = set()
    numbers: dict[str, int] = {}
    for scenario, fixture in _FIXTURES.items():
        match = _RC_TAG_RE.match(fixture["tag"])
        assert match is not None
        cores.add(match.group(1))
        numbers[scenario] = int(match.group(2))
    assert_that(cores).is_length(1)
    ordered = [numbers[s] for s in _SCENARIOS if s in numbers and s != "S4"]
    assert_that(ordered).is_equal_to(sorted(ordered))
    # S4 records S3's run (the recovery resumes it), so it shares the id.
    run_ids = [
        fixture["run"]["id"]
        for scenario, fixture in _FIXTURES.items()
        if scenario != "S4"
    ]
    assert_that(run_ids).is_length(len(set(run_ids)))


# --- S1 green ----------------------------------------------------------------


def test_s1_green_run_publishes_every_channel_but_homebrew() -> None:
    """S1: green run, every artifact class present, npm under ``next``."""
    fixture = _fixture("S1")
    assert_that(fixture["run"]["conclusion"]).is_equal_to("success")
    jobs = fixture["jobs"]
    for job_id in (*_BUILD_JOBS, "release-gate", *_PUBLISH_JOBS, "notify-failure"):
        assert_that(jobs[job_id]).described_as(job_id).is_equal_to("success")
    for job_id in _STABLE_ONLY_JOBS:
        assert_that(jobs[job_id]).described_as(job_id).is_equal_to("skipped")
    published = fixture["published"]
    _assert_channels_present(published, version=fixture["version"])
    _assert_npm_present(
        published,
        npm_version=fixture["npm_version"],
        assets=published["github_release"]["assets"],
    )
    assert_that(fixture["issues"]["release_failure"]).is_none()
    assert_that(fixture["wall_clock_seconds"]).is_instance_of(int)
    assert_that(fixture["wall_clock_seconds"]).is_greater_than(0)
    assert_that(fixture["recovery"]).is_none()


def test_s1_records_the_policy_verification_output() -> None:
    """S1 copies every verify command's output into the fixture."""
    fixture = _fixture("S1")
    verification = fixture.get("verification")
    assert_that(verification).is_instance_of(dict)
    assert verification is not None
    commands = " ".join(verification)
    for needle in (
        "gh attestation verify",
        "cosign verify",
        "sha256sum -c SHA256SUMS",
        "npm audit signatures",
    ):
        assert_that(commands).described_as(needle).contains(needle)
    for command, output in verification.items():
        assert_that(output).described_as(command).is_instance_of(str)
        assert_that(output.strip()).described_as(command).is_not_empty()


# --- S2 build failure --------------------------------------------------------


def test_s2_build_failure_publishes_nothing() -> None:
    """S2: the gate fails on the injected fault and no channel receives anything."""
    fixture = _fixture("S2")
    assert_that(fixture["run"]["conclusion"]).is_equal_to("failure")
    jobs = fixture["jobs"]
    assert_that(jobs["release-gate"]).is_equal_to("failure")
    for job_id in (*_PUBLISH_JOBS, *_STABLE_ONLY_JOBS):
        assert_that(jobs[job_id]).described_as(job_id).is_equal_to("skipped")
    assert_that(jobs["notify-failure"]).is_not_equal_to("skipped")
    published = fixture["published"]
    for channel in ("pypi", "github_release", "docker", "npm"):
        assert_that(_is_empty(published[channel])).described_as(channel).is_true()
    # A pre-publish failure is not an incident: nothing filed, or filed and
    # closed, never an open issue.
    issue = fixture["issues"]["release_failure"]
    if issue is not None:
        assert_that(issue["state"]).is_equal_to("closed")
    assert_that(fixture["issues"]["closing_comment"]).is_none()
    assert_that(fixture["wall_clock_seconds"]).is_none()
    assert_that(fixture["recovery"]).is_none()


# --- S3 publish failure ------------------------------------------------------


def test_s3_publish_failure_files_the_issue_and_leaves_npm_empty() -> None:
    """S3: PyPI, release and Docker present, npm absent, one attempt, issue filed."""
    fixture = _fixture("S3")
    assert_that(fixture["run"]["conclusion"]).is_equal_to("failure")
    assert_that(fixture["run"]["attempt"]).is_equal_to(1)
    jobs = fixture["jobs"]
    for job_id in ("release-gate", "pypi-upload", "github-release", "docker-promote"):
        assert_that(jobs[job_id]).described_as(job_id).is_equal_to("success")
    assert_that(jobs["npm-publish"]).is_equal_to("failure")
    for job_id in _STABLE_ONLY_JOBS:
        assert_that(jobs[job_id]).described_as(job_id).is_equal_to("skipped")
    assert_that(jobs["notify-failure"]).is_equal_to("success")
    published = fixture["published"]
    _assert_channels_present(published, version=fixture["version"])
    assert_that(_is_empty(published["npm"])).is_true()
    issue = fixture["issues"]["release_failure"]
    assert_that(issue).is_not_none()
    assert issue is not None
    assert_that(issue["url"]).contains("/issues/")
    assert_that(fixture["wall_clock_seconds"]).is_none()
    assert_that(fixture["recovery"]).is_none()


# --- S4 recovery -------------------------------------------------------------


def test_s4_recovery_is_refused_for_a_prerelease_and_changes_nothing() -> None:
    """S4: the dry run applies the prerelease exemption; nothing moves after S3."""
    s3 = _fixture("S3")
    s4 = _fixture("S4")
    assert_that(s4["tag"]).is_equal_to(s3["tag"])
    recovery = s4["recovery"]
    assert_that(recovery).is_not_none()
    assert recovery is not None
    assert_that(set(recovery)).is_equal_to(_RECOVERY_KEYS)
    assert_that(recovery["source_run_id"]).is_equal_to(s3["run"]["id"])
    dry_run = recovery["dry_run"]
    assert_that(set(dry_run)).is_equal_to(_DRY_RUN_KEYS | _DRY_RUN_REFUSAL_KEYS)
    assert_that(dry_run["url"]).ends_with(f"/actions/runs/{dry_run['id']}")
    assert_that(dry_run["id"]).is_not_equal_to(s3["run"]["id"])
    # The rule is the assertion; the message is how the tooling voiced it.
    assert_that(dry_run["policy"]).is_equal_to(_PRERELEASE_EXEMPTION)
    assert_that(dry_run["conclusion"]).is_equal_to("failure")
    assert_that(dry_run["channels"]).is_equal_to([])
    assert_that(dry_run["refusal"]).contains("refusing to recover prerelease tag")
    assert_that(dry_run["refusal"]).contains("prereleases are abandoned by policy")
    assert_that(recovery["live"]).is_none()
    # The record of the ORIGINAL run is unchanged by the refused recovery.
    assert_that(s4["run"]).is_equal_to(s3["run"])
    assert_that(s4["jobs"]).is_equal_to(s3["jobs"])
    published = s4["published"]
    assert_that(published["pypi"]).is_equal_to(s3["published"]["pypi"])
    assert_that(published["github_release"]).is_equal_to(
        s3["published"]["github_release"],
    )
    assert_that(published["docker"]).is_equal_to(s3["published"]["docker"])
    assert_that(_is_empty(published["npm"])).is_true()
    issue = s4["issues"]["release_failure"]
    assert_that(issue).is_not_none()
    assert issue is not None
    assert_that(issue["url"]).is_equal_to(s3["issues"]["release_failure"]["url"])
    assert_that(issue["state"]).is_equal_to("closed")
    assert_that(issue["closed_by"].strip()).is_not_empty()
    assert_that(issue["closed_at"]).matches(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    assert_that(s4["issues"]["closing_comment"]).is_not_none()
    assert_that(str(s4["issues"]["closing_comment"])).starts_with(issue["url"])
    # The runbook's caveat travels with the evidence.
    notes = s4["notes"].lower()
    assert_that(notes).contains("exempt")
    assert_that(notes).contains("policy")
