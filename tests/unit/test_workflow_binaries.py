"""Workflow wiring contracts: binaries."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from assertpy import assert_that

from tests.unit.workflow_wiring_helpers import (
    _BINARY_WORKFLOWS,
    _BUILD_BINARY_WORKFLOW,
    _BUILD_STAGE_JOBS,
    _NPM_PLATFORM_KEYS,
    _PUBLISH_BINARIES_WORKFLOW,
    _PUBLISH_JOBS,
    _RELEASE_BINARY_ARTIFACTS,
    _REPO_ROOT,
    _REUSE_GUARD,
    _REUSE_SKIPPED_STEPS,
    _REUSE_UNGATED_STEPS,
    _SHA_PIN_RE,
    _job_ancestors,
    _load_workflow,
    _normalize_github_expr,
    _quoted_strings_in_block,
    _tools_dockerfile_uv_version,
    _uv_commands,
)


def test_build_binary_pins_setup_uv_version() -> None:
    """Binary builds must pin setup-uv to an exact version, not latest.

    ``version: latest`` forces astral-sh/setup-uv to fetch
    astral-sh/versions uv.ndjson, which has timed out repeatedly under
    harden-runner during tag publishes (#1487). An exact pin uses the
    ExactVersionResolver path; the continue-on-error retry remains.

    The pin must also equal the tools image's ``ARG UV_VERSION`` so the two
    Renovate-managed uv pins cannot silently drift apart.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    pinned = workflow["env"]["UV_VERSION"]
    assert_that(pinned).matches(r"^\d+\.\d+\.\d+$")
    assert_that(pinned).described_as(
        "build-binaries UV_VERSION must match docker/tools.Dockerfile ARG UV_VERSION",
    ).is_equal_to(_tools_dockerfile_uv_version())

    setup_uv_steps: list[dict[str, Any]] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            uses = step.get("uses") or ""
            if "astral-sh/setup-uv@" in uses:
                setup_uv_steps.append(step)

    assert_that(setup_uv_steps).is_not_empty()
    for step in setup_uv_steps:
        version = step.get("with", {}).get("version", "")
        assert_that(version).does_not_contain("latest")
        assert_that(version).contains("env.UV_VERSION")


@pytest.mark.parametrize("workflow_name", _BINARY_WORKFLOWS)
def test_binary_workflows_are_workflow_call_only(workflow_name: str) -> None:
    """Neither binary stage can be dispatched by hand (#2562, #2484).

    A dispatch of the old workflow resolved the latest published release and
    could republish main-HEAD binaries onto it. Both halves of the split take
    the tag from their caller and nowhere else: ``workflow_call`` is the only
    trigger, ``release_tag`` is required, and the ``upload_to_release`` repair
    input no longer exists anywhere.

    Args:
        workflow_name: The binary workflow under test.
    """
    workflow = _load_workflow(name=workflow_name)
    triggers = workflow["on"]
    assert_that(set(triggers)).described_as(workflow_name).is_equal_to(
        {"workflow_call"},
    )
    release_tag = triggers["workflow_call"]["inputs"]["release_tag"]
    assert_that(release_tag["required"]).described_as(workflow_name).is_true()
    assert_that(release_tag["type"]).described_as(workflow_name).is_equal_to("string")
    assert_that(triggers["workflow_call"]["inputs"]).does_not_contain_key(
        "upload_to_release",
    )
    text = (_REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(
        encoding="utf-8",
    )
    assert_that(text).described_as(workflow_name).does_not_contain("upload_to_release")
    assert_that(text).described_as(workflow_name).does_not_contain("workflow_dispatch")


def test_readme_no_longer_documents_the_binary_repair_dispatch() -> None:
    """The workflows README describes the two-stage split, not a dispatch.

    The "Dispatching build-binary.yml by hand" runbook told an operator how
    to republish onto the latest release; with the trigger gone that text
    would send them to a form that no longer exists (#2562). The README has
    to name both stages, the recovery pointer, and the rerun path instead.
    """
    readme = (_REPO_ROOT / ".github" / "workflows" / "README.md").read_text(
        encoding="utf-8",
    )
    for stale in ("upload_to_release", "### Dispatching", "Repair dispatch"):
        assert_that(readme).described_as(stale).does_not_contain(stale)
    for expected in (
        _BUILD_BINARY_WORKFLOW,
        _PUBLISH_BINARIES_WORKFLOW,
        "lgtm-hq/lgtm-ci#966",
        "Re-run failed jobs",
        "attest-build-provenance",
        "90 days",
    ):
        assert_that(readme).described_as(expected).contains(expected)


def test_build_binary_retries_setup_uv_on_failure() -> None:
    """Each setup-uv job keeps a continue-on-error + retry pair (#1513)."""
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    jobs_with_setup_uv = [
        job_id
        for job_id, job in workflow["jobs"].items()
        if any(
            "astral-sh/setup-uv@" in (step.get("uses") or "")
            for step in job.get("steps") or []
        )
    ]
    assert_that(jobs_with_setup_uv).is_not_empty()

    for job_id in jobs_with_setup_uv:
        steps = workflow["jobs"][job_id]["steps"]
        first = next(step for step in steps if step.get("id") == "setup-uv")
        assert_that(first.get("continue-on-error")).is_true()
        retry = next(step for step in steps if step.get("name") == "Install uv (retry)")
        assert_that(retry.get("if")).contains("steps.setup-uv.outcome == 'failure'")


def test_binary_jobs_never_install_the_dev_group() -> None:
    """Binary builds must opt out of the default dependency groups (#1897).

    The default ``dev`` group pulls ``mcp`` -> ``pyjwt[crypto]`` ->
    ``cryptography``, which ships no macOS x86_64 wheel from 49.0.0 on. Syncing
    it makes uv build that package from source via maturin/cargo, which then
    dies on the (deliberately) blocked crates.io egress. Every ``uv`` command in
    the binary jobs therefore has to carry ``--no-default-groups`` -- including
    ``uv run``, which otherwise re-syncs the default groups back in.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    binary_jobs = ("build-macos", "build-linux")

    for job_id in binary_jobs:
        steps = workflow["jobs"][job_id]["steps"]
        uv_commands = [
            command
            for step in steps
            for command in _uv_commands(script=step.get("run") or "")
        ]
        assert_that(uv_commands).described_as(f"{job_id} uv commands").is_not_empty()

        for tokens in uv_commands:
            rendered = " ".join(tokens)
            groups = [
                value
                for flag, value in zip(tokens, tokens[1:], strict=False)
                if flag == "--group"
            ]
            assert_that(groups).described_as(
                f"{job_id}: {rendered!r} must not sync the dev group",
            ).does_not_contain("dev")
            assert_that(tokens).described_as(
                f"{job_id}: {rendered!r} must pass --no-default-groups",
            ).contains("--no-default-groups")


def test_build_linux_carries_no_githubapp_grant() -> None:
    """The Linux binary build must not allowlist githubapp.com (#2339, #2488).

    harden-runner v2.21.0 block mode denied ``hosted-compute-watchdog-*`` and
    ``hosted-compute-request-orchestrator-*`` under githubapp.com, and the x64
    build was reclaimed mid-run on every attempt for v0.147.7 with "The runner
    has received a shutdown signal" (#1761, #2339). #2487 answered with a
    literal shard list. v2.21.1 allows ``*.githubapp.com`` from GitHub meta on
    its own, so the list is gone and no githubapp.com entry, literal or glob,
    may return. No glob of any kind is permitted on this job either.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    job = workflow["jobs"]["build-linux"]
    harden = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("step-security/harden-runner@")
    )
    endpoints = str(harden["with"]["allowed-endpoints"]).split()

    assert_that(harden["with"]["egress-policy"]).is_equal_to("audit")
    for endpoint in endpoints:
        assert_that(endpoint.lower()).described_as(endpoint).does_not_contain(
            "githubapp.com",
        )
        assert_that(endpoint).described_as(
            f"{endpoint}: no glob may widen the block policy on this job",
        ).does_not_contain("*")
    # The job must still carry its baseline: build-binaries.yml is read from
    # the tag, so a shrunk list passes every PR and fails at the release.
    assert_that(endpoints).contains(
        "pypi.org:443",
        "files.pythonhosted.org:443",
        "nuitka.net:443",
        "release-assets.githubusercontent.com:443",
    )
    assert_that(endpoints).does_not_contain_duplicates()


# --- Binary build job timeouts (#1702) ---------------------------------------
#
# No job in the former build-binary.yml set timeout-minutes, so every binary
# build inherited GitHub's 6-hour default. The Linux x64 Nuitka compile twice
# hung until runner loss at ~57 min (v0.80.4, v0.91.24), silently desyncing
# the npm publish and Homebrew chain via `needs:`.


@pytest.mark.parametrize("workflow_name", _BINARY_WORKFLOWS)
def test_binary_workflows_every_job_declares_timeout_minutes(
    workflow_name: str,
) -> None:
    """Every binary-stage job is bounded instead of inheriting the 6h default.

    Args:
        workflow_name: The binary workflow under test.
    """
    workflow = _load_workflow(name=workflow_name)
    for job_id, job in workflow["jobs"].items():
        assert_that(job).described_as(job_id).contains_key("timeout-minutes")
        assert_that(job["timeout-minutes"]).described_as(job_id).is_instance_of(int)


def test_build_binary_compile_step_has_step_level_timeout() -> None:
    """The Build binary step is bounded tighter than its job.

    A stalled compile must be attributable to the compile itself (25 min vs
    the observed healthy norm of 16-24 min), not surface as a whole-job
    timeout with no failed step.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        steps = workflow["jobs"][job_id]["steps"]
        build_steps = [step for step in steps if step.get("name") == "Build binary"]
        assert_that(build_steps).described_as(job_id).is_length(1)
        assert_that(build_steps[0]["timeout-minutes"]).is_equal_to(25)


def test_build_binary_job_timeout_leaves_diagnostic_headroom() -> None:
    """The job deadline must not preempt the failure diagnostics.

    Setup (harden-runner, checkout, setup-python, uv sync) can consume five
    minutes or more, so a job deadline only 5 minutes past the 25-minute
    compile bound can arrive during the post-timeout evidence steps and kill
    the runner before the OOM artifacts upload. Require at least 10 minutes
    of non-compile budget.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        job = workflow["jobs"][job_id]
        build_steps = [
            step for step in job["steps"] if step.get("name") == "Build binary"
        ]
        headroom = job["timeout-minutes"] - build_steps[0]["timeout-minutes"]
        assert_that(headroom).described_as(job_id).is_greater_than_or_equal_to(10)


def test_build_binary_job_timeout_covers_compile_and_smoke() -> None:
    """The job deadline must cover compile + smoke + diagnostic headroom.

    The smoke-test step can use 20 minutes on its own. A 35-minute job with a
    25-minute compile bound can cancel a valid smoke test before that step's
    own timeout fires.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        job = workflow["jobs"][job_id]
        steps = job["steps"]
        compile_timeout = next(
            step["timeout-minutes"]
            for step in steps
            if step.get("name") == "Build binary"
        )
        smoke_timeout = next(
            step["timeout-minutes"]
            for step in steps
            if step.get("name") == "Smoke-test tool registry"
        )
        headroom = job["timeout-minutes"] - compile_timeout - smoke_timeout
        assert_that(headroom).described_as(job_id).is_greater_than_or_equal_to(10)


def test_build_binary_ships_exactly_three_platform_binaries() -> None:
    """The binary stages build macOS arm64 plus both Linux arches, nothing else.

    No x86_64 macOS leg, no universal job, no ``arch`` input to select either:
    the job lists of both stages, the macOS matrix, the publish matrix and the
    caller's ``with:`` blocks are all pinned so a partial revert of #2579 is
    caught here rather than on a tag.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    assert_that(set(workflow["jobs"])).is_equal_to(
        {"generate-man-page", "build-macos", "build-linux"},
    )
    publish = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    # PR (c) of #2562: the release reusable attaches the binaries from the
    # gate's artifact, so the publish stage is the tap dispatch alone.
    assert_that(set(publish["jobs"])).is_equal_to(
        {"get-release-info", "homebrew-dispatch"},
    )

    macos = workflow["jobs"]["build-macos"]
    assert_that(macos["strategy"]["matrix"]).is_equal_to({"arch": ["arm64"]})
    assert_that(str(macos["runs-on"])).does_not_contain("intel")
    assert_that(str(macos["runs-on"])).does_not_contain("x86_64")

    linux = workflow["jobs"]["build-linux"]
    linux_arches = sorted(
        entry["arch"] for entry in linux["strategy"]["matrix"]["include"]
    )
    assert_that(linux_arches).is_equal_to(["arm64", "x64"])

    for workflow_name in _BINARY_WORKFLOWS:
        text = (_REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8",
        )
        for stale in (
            "lintro-macos-x86_64",
            "sha256-x86_64",
            "universal",
            "lipo",
            "macos-15-intel",
        ):
            # Only the #2579 rationale comment may mention the dropped leg.
            occurrences = [
                line
                for line in text.splitlines()
                if stale in line and not line.lstrip().startswith("#")
            ]
            assert_that(occurrences).described_as(
                f"{stale!r} must not appear outside comments in {workflow_name}",
            ).is_empty()

    caller = _load_workflow(name="publish-pypi-on-tag.yml")
    for job_id, callee in (
        ("build-binaries", _BUILD_BINARY_WORKFLOW),
        ("homebrew-tap", _PUBLISH_BINARIES_WORKFLOW),
    ):
        job = caller["jobs"][job_id]
        assert_that(job["uses"]).described_as(job_id).is_equal_to(
            f"./.github/workflows/{callee}",
        )
        assert_that(job["with"]).described_as(job_id).is_equal_to(
            {"release_tag": "${{ github.ref_name }}"},
        )


def test_homebrew_dispatch_carries_one_macos_checksum() -> None:
    """The tap dispatch sends the arm64 checksum and no x86_64 one (#2579).

    The formula's Intel branch installs from PyPI and needs no asset checksum,
    so a second value here would either be fabricated or read from an artifact
    no job produces any more.
    """
    workflow = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    job = workflow["jobs"]["homebrew-dispatch"]
    # The binaries are on the release before this workflow is even called
    # (github-release is upstream of the caller job); the checksum comes from
    # the manifest the release gate wrote after verifying the asset (#2562).
    assert_that(job["needs"]).is_equal_to(["get-release-info"])
    by_name = {step.get("name"): step for step in job["steps"]}

    download = by_name["Download release manifest"]
    assert_that(download["uses"]).contains("actions/download-artifact@")
    assert_that(download["with"]).is_equal_to(
        {"name": "release-manifest", "path": "manifest/"},
    )

    read = by_name["Read checksum from the release manifest"]
    assert_that(read["id"]).is_equal_to("checksums")
    assert_that(read["run"]).contains("scripts/ci/release-gate/read_manifest_sha.sh")
    assert_that(read["run"]).contains("lintro-macos-arm64")
    assert_that(read["env"]["MANIFEST"]).is_equal_to("manifest/release-manifest.json")
    assert_that(read["run"]).does_not_contain("x86_64")

    dispatch = by_name["Dispatch formula update"]
    assert_that(dispatch["uses"]).contains("trigger-homebrew-update@")
    payload = dispatch["with"]
    assert_that(payload["binary-arm64-sha"]).is_equal_to(
        "${{ steps.checksums.outputs.arm64_sha256 }}",
    )
    assert_that(payload).does_not_contain_key("binary-x86-sha")
    assert_that(set(payload)).is_equal_to(
        {"formula", "version", "pypi-package", "binary-arm64-sha", "token"},
    )

    # The build-macos matrix is what makes ``sha256-arm64`` exist at all.
    build = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    macos_arches = build["jobs"]["build-macos"]["strategy"]["matrix"]["arch"]
    assert_that(macos_arches).is_equal_to(["arm64"])


def test_npm_platform_map_has_no_intel_macos_package() -> None:
    """Every npm platform list agrees on the three shipped platforms (#2579).

    The staging map, the version-sync list, the publish order, the on-disk
    package tree, the meta-package's optional dependencies, the release
    download list and the resolver's map are seven spellings of one fact. If
    any of them kept ``darwin-x64`` the tag run would fail on a missing asset
    or publish a meta-package pointing at a platform package that never ships.
    """
    npm_scripts = _REPO_ROOT / "scripts" / "ci" / "npm"

    stage = (npm_scripts / "stage_binaries.py").read_text(encoding="utf-8")
    stage_pairs = re.findall(
        r'"(lintro-[a-z0-9_-]+)":\s*"([a-z0-9-]+)"',
        stage.partition("BINARY_MAP")[2].partition("}")[0],
    )
    assert_that(dict(stage_pairs)).is_equal_to(
        {
            "lintro-macos-arm64": "darwin-arm64",
            "lintro-linux-arm64": "linux-arm64",
            "lintro-linux-x64": "linux-x64",
        },
    )
    assert_that(sorted(dict(stage_pairs))).is_equal_to(
        sorted(_RELEASE_BINARY_ARTIFACTS),
    )

    download = (npm_scripts / "download_release_binaries.sh").read_text(
        encoding="utf-8",
    )
    assert_that(
        sorted(_quoted_strings_in_block(download, start="binaries=(", end=")")),
    ).is_equal_to(sorted(_RELEASE_BINARY_ARTIFACTS))

    sync = (npm_scripts / "sync_npm_version.py").read_text(encoding="utf-8")
    assert_that(
        sorted(_quoted_strings_in_block(sync, start="PLATFORM_PACKAGES = (", end=")")),
    ).is_equal_to(sorted(_NPM_PLATFORM_KEYS))

    # The publish order now lives in the reusable call (#2632): platform
    # packages first, meta package last.
    publish_order = json.loads(
        _load_workflow(name="publish-npm.yml")["jobs"]["publish"]["with"]["order"],
    )
    assert_that(publish_order).is_equal_to([*sorted(_NPM_PLATFORM_KEYS), "lintro"])

    npm_dir = _REPO_ROOT / "npm"
    on_disk = sorted(child.name for child in npm_dir.iterdir() if child.is_dir())
    assert_that(on_disk).is_equal_to(sorted([*_NPM_PLATFORM_KEYS, "lintro"]))

    meta = json.loads(
        (npm_dir / "lintro" / "package.json").read_text(encoding="utf-8"),
    )
    assert_that(sorted(meta["optionalDependencies"])).is_equal_to(
        sorted(f"@lgtm-hq/lintro-{key}" for key in _NPM_PLATFORM_KEYS),
    )

    resolver = (npm_dir / "lintro" / "lib" / "resolve.js").read_text(encoding="utf-8")
    resolver_map = dict(
        re.findall(
            r"'([a-z0-9-]+)':\s*'(@lgtm-hq/lintro-[a-z0-9-]+)'",
            resolver.partition("PLATFORM_PACKAGES = Object.freeze({")[2].partition(
                "});",
            )[0],
        ),
    )
    assert_that(resolver_map).is_equal_to(
        {key: f"@lgtm-hq/lintro-{key}" for key in _NPM_PLATFORM_KEYS},
    )

    # The Intel pointer: a documented dead end, not a silent one.
    hints = resolver.partition("UNSUPPORTED_PLATFORM_HINTS = Object.freeze({")[
        2
    ].partition("});")[0]
    assert_that(hints).contains("'darwin-x64'")
    assert_that(hints).contains("brew install lintro")
    assert_that(hints).contains("pip install lintro")


def test_build_binary_compile_is_wrapped_by_memory_sampler() -> None:
    """Build binary is bracketed by the #1707 sampler with failure-only upload.

    The Linux x64 Nuitka compile repeatedly dies of runner loss with no
    retrievable log, so the sampler (memory-sampler.sh) and the OOM evidence
    collector (collect-oom-evidence.sh) must stay wired around the compile:
    sampler stopped via ``always()``, evidence + artifact upload failure-only.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        steps = workflow["jobs"][job_id]["steps"]
        by_name = {step.get("name"): step for step in steps}

        start = by_name["Start memory sampler"]
        assert_that(start["run"]).is_equal_to(
            "scripts/ci/memory-sampler.sh start memory-sampler.log memory-sampler.pid",
        )

        stop = by_name["Stop memory sampler"]
        assert_that(stop["run"]).is_equal_to(
            "scripts/ci/memory-sampler.sh stop memory-sampler.log memory-sampler.pid",
        )
        # always(): the sampler must not outlive a failed compile, and the
        # final snapshot it appends is part of the failure evidence.
        assert_that(stop["if"]).is_equal_to("always()")

        collect = by_name["Collect OOM evidence"]
        assert_that(collect["if"]).is_equal_to("failure()")
        assert_that(collect["run"]).is_equal_to(
            "scripts/ci/collect-oom-evidence.sh oom-evidence.txt",
        )

        upload = by_name["Upload memory diagnostics"]
        assert_that(upload["if"]).is_equal_to("failure()")
        assert_that(upload["uses"]).contains("actions/upload-artifact@")
        assert_that(str(upload["with"]["name"])).contains("matrix.arch")
        assert_that(upload["with"]["path"]).contains("memory-sampler.log")
        assert_that(upload["with"]["path"]).contains("oom-evidence.txt")

        # Ordering: sampler starts right before the compile and stops right
        # after, so the log brackets exactly the compile window.
        names = [step.get("name") for step in steps]
        assert_that(names.index("Start memory sampler")).is_equal_to(
            names.index("Build binary") - 1,
        )
        assert_that(names.index("Stop memory sampler")).is_equal_to(
            names.index("Build binary") + 1,
        )

    # The wired scripts exist and stay shellcheck/actionlint-clean via lintro.
    assert_that(
        (_REPO_ROOT / "scripts/ci/memory-sampler.sh").is_file(),
    ).is_true()
    assert_that(
        (_REPO_ROOT / "scripts/ci/collect-oom-evidence.sh").is_file(),
    ).is_true()


def test_build_binary_checks_for_a_reusable_release_asset() -> None:
    """Both per-arch jobs consult the release before compiling.

    The check runs before ``Build binary`` and passes the platform's asset
    name, the same-run checksum artifact, and the finalized destination path.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    expected_args = {
        "build-macos": (
            "lintro-macos-$BUILD_ARCH",
            "sha256-$BUILD_ARCH",
            "dist/nuitka/lintro-macos-$BUILD_ARCH",
        ),
        "build-linux": (
            "lintro-linux-$BUILD_ARCH",
            "sha256-linux-$BUILD_ARCH",
            "dist/nuitka/lintro-linux-$BUILD_ARCH",
        ),
    }
    for job_id, (asset, artifact, dest) in expected_args.items():
        steps = workflow["jobs"][job_id]["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps}

        check = by_name["Check for reusable release asset"]
        assert_that(check["id"]).described_as(job_id).is_equal_to("reuse")
        assert_that(check["run"]).described_as(job_id).contains(
            "scripts/build/reuse_release_asset.sh",
        )
        for token in (asset, artifact, dest):
            assert_that(check["run"]).described_as(job_id).contains(token)
        assert_that(check["env"]).described_as(job_id).contains_key(
            "GH_TOKEN",
            "RELEASE_TAG",
            "BUILD_ARCH",
        )
        assert_that(names.index("Check for reusable release asset")).described_as(
            job_id,
        ).is_less_than(names.index("Build binary"))


def test_build_binary_skips_the_rebuild_when_the_asset_is_reused() -> None:
    """Reuse skips build/verify/smoke/finalize but never the artifact uploads.

    Verify and smoke-test are safe to skip only because the checksum that
    authorised the reuse came from a same-run artifact written after those two
    steps passed on an earlier attempt of the same job.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        by_name = {step.get("name"): step for step in workflow["jobs"][job_id]["steps"]}
        for step_name in _REUSE_SKIPPED_STEPS:
            assert_that(_normalize_github_expr(by_name[step_name]["if"])).described_as(
                f"{job_id}:{step_name}",
            ).is_equal_to(_REUSE_GUARD)
        for step_name in _REUSE_UNGATED_STEPS:
            assert_that(by_name[step_name].get("if")).described_as(
                f"{job_id}:{step_name}",
            ).is_none()


def test_build_binary_save_sha256_falls_back_to_the_reused_checksum() -> None:
    """Finalize binary is skipped on reuse, so its output cannot be the only source."""
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        by_name = {step.get("name"): step for step in workflow["jobs"][job_id]["steps"]}
        sha_env = by_name["Save SHA256 to file"]["env"]["SHA256"]
        # The exact expression, not just both names: the order matters (the
        # freshly built checksum wins) and `||` is what makes the reuse value a
        # fallback rather than an override.
        assert_that(_normalize_github_expr(sha_env)).described_as(
            job_id,
        ).is_equal_to(
            "${{ steps.sha256.outputs.sha256 || steps.reuse.outputs.sha256 }}",
        )


def test_build_binary_jobs_may_read_their_own_run_artifacts() -> None:
    """The reuse check needs the release (contents) and the run's artifacts.

    Since #2562 the build jobs are read-only on contents and hold the two
    attestation scopes instead; the publish upload legs hold ``contents:
    write`` and the same ``actions: read``.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        permissions = workflow["jobs"][job_id]["permissions"]
        assert_that(permissions).described_as(job_id).is_equal_to(
            {
                "contents": "read",
                "actions": "read",
                "id-token": "write",
                "attestations": "write",
            },
        )

    publish = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    permissions = publish["jobs"]["homebrew-dispatch"]["permissions"]
    assert_that(permissions).is_equal_to({"contents": "read"})


def test_build_binaries_attest_the_finalized_binary() -> None:
    """Each build job attests the finalized binary and hard-fails on error.

    The attestation must cover the exact file the artifact upload ships, sit
    after ``Finalize binary`` (so the subject is the renamed, final binary)
    and before ``Upload artifact``, and carry no ``continue-on-error``: an
    unattested binary must fail the build rather than ship (#2562).
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        steps = workflow["jobs"][job_id]["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps}

        attest = by_name["Attest build provenance"]
        assert_that(str(attest["uses"])).described_as(job_id).starts_with(
            "actions/attest-build-provenance@",
        )
        assert_that(_SHA_PIN_RE.search(str(attest["uses"]))).described_as(
            job_id,
        ).is_not_none()
        assert_that(attest).described_as(job_id).does_not_contain_key(
            "continue-on-error",
        )
        assert_that(attest["with"]["subject-path"]).described_as(job_id).is_equal_to(
            by_name["Upload artifact"]["with"]["path"],
        )
        assert_that(names.index("Finalize binary")).described_as(job_id).is_less_than(
            names.index("Attest build provenance"),
        )
        assert_that(names.index("Attest build provenance")).described_as(
            job_id,
        ).is_less_than(names.index("Upload artifact"))
        # The reuse invariant behind _REUSE_SKIPPED_STEPS: the same-run
        # checksum is written and uploaded only after the attestation, so a
        # checksum match on a later attempt proves the bytes are attested.
        for checksum_step in ("Save SHA256 to file", "Upload SHA256 file"):
            assert_that(names.index("Attest build provenance")).described_as(
                f"{job_id}: attest must precede {checksum_step}",
            ).is_less_than(names.index(checksum_step))

    # Whole-workflow sweep: no attest step anywhere in the build stage may be
    # best-effort, whatever it is called.
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps") or []:
            if "attest-build-provenance" in str(step.get("uses", "")):
                assert_that(step.get("continue-on-error")).described_as(
                    f"{job_id}:{step.get('name')}",
                ).is_none()


def test_binary_artifacts_are_retained_for_the_recovery_window() -> None:
    """Every release artifact of the build stage is retained for 90 days.

    Seven days was shorter than the time a broken release can sit before a
    publish rerun needs the built and attested bytes (#2562, policy window
    lgtm-hq/lgtm-ci#962). Failure diagnostics keep the short retention.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    retained: dict[str, int] = {}
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            if not str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                continue
            retained[str(step["with"]["name"])] = int(step["with"]["retention-days"])

    assert_that(retained).is_equal_to(
        {
            "lintro-man-page": 90,
            "lintro-macos-${{ matrix.arch }}": 90,
            "sha256-${{ matrix.arch }}": 90,
            "memory-diagnostics-macos-${{ matrix.arch }}": 7,
            "lintro-linux-${{ matrix.arch }}": 90,
            "sha256-linux-${{ matrix.arch }}": 90,
            "memory-diagnostics-linux-${{ matrix.arch }}": 7,
        },
    )


def test_binary_publish_jobs_allowlist_the_artifact_service() -> None:
    """Every hardened publish job that moves an artifact can reach the service.

    Block-mode harden-runner denies ``actions/download-artifact`` its hops to
    ``pipelines.actions.githubusercontent.com`` and
    ``results-receiver.actions.githubusercontent.com`` unless both are
    allowlisted, and the failure only shows on a tag run (#2562 review).
    """
    for workflow_name in _BINARY_WORKFLOWS:
        workflow = _load_workflow(name=workflow_name)
        for job_id, job in workflow["jobs"].items():
            steps = job.get("steps") or []
            harden = next(
                (
                    step
                    for step in steps
                    if str(step.get("uses", "")).startswith("step-security/")
                ),
                None,
            )
            assert_that(harden).described_as(f"{workflow_name}:{job_id}").is_not_none()
            assert harden is not None
            assert_that(harden["with"]["egress-policy"]).is_equal_to("audit")
            moves_artifact = any(
                str(step.get("uses", "")).startswith(
                    ("actions/upload-artifact@", "actions/download-artifact@"),
                )
                for step in steps
            )
            if not moves_artifact:
                continue
            endpoints = str(harden["with"]["allowed-endpoints"]).split()
            assert_that(endpoints).described_as(f"{workflow_name}:{job_id}").contains(
                "pipelines.actions.githubusercontent.com:443",
                "results-receiver.actions.githubusercontent.com:443",
            )


def test_build_stage_reaches_pypi_upload_only_through_the_gate() -> None:
    """Build jobs feed the PyPI upload only via release-gate, never wait on it.

    PR (a)/(b) asserted no build job was upstream of ``pypi-upload`` while
    the builds still ran after the gate. PR (c) puts them ahead of it by
    design, so the invariant becomes: the upload's only direct dependency is
    the gate, every build job is upstream of the gate, and no build job has
    the upload (or anything published) among its own ancestors.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    assert_that(publish["jobs"]["pypi-upload"]["needs"]).is_equal_to(["release-gate"])
    gate_upstream = _job_ancestors(publish, job_id="release-gate")
    assert_that(gate_upstream).contains(*_BUILD_STAGE_JOBS)
    for job_id in (*_BUILD_STAGE_JOBS, "classify-tag"):
        ancestors = _job_ancestors(publish, job_id=job_id)
        assert_that(ancestors).described_as(job_id).does_not_contain(
            "release-gate",
            *_PUBLISH_JOBS,
        )
    # The build calls run for prereleases too (artifacts only); the publish
    # calls stay stable-only.
    build_if = _normalize_github_expr(str(publish["jobs"]["build-binaries"]["if"]))
    assert_that(build_if).does_not_contain("is_prerelease")
    publish_if = _normalize_github_expr(str(publish["jobs"]["homebrew-tap"]["if"]))
    assert_that(publish_if).contains(
        "needs.classify-tag.outputs.is_prerelease == 'false'",
    )


def test_binary_release_scripts_are_executable() -> None:
    """The #2435 scripts the binary stages reference exist and are executable."""
    scripts = (
        _REPO_ROOT / "scripts" / "build" / "reuse_release_asset.sh",
        _REPO_ROOT / "scripts" / "build" / "upload_release_asset.sh",
    )
    for script in scripts:
        assert_that(script.exists()).described_as(str(script)).is_true()
        assert_that(script.stat().st_mode & 0o111).described_as(
            f"{script} is not executable",
        ).is_not_zero()


def test_memory_sampler_tees_its_output_into_the_step_log() -> None:
    """Sampler evidence reaches stdout, not only the failure-only artifact.

    ``start`` tees a baseline snapshot into its own step log, so a runner kill
    still leaves the memory state the compile began from. The interval samples
    reach a human only through ``Stop memory sampler`` (``if: always()``, which
    replays the log) or the ``if: failure()`` artifact, neither of which runs
    when the runner itself is killed. Both steps must therefore stay wired, and
    the artifact upload must stay failure-only rather than becoming the sole
    channel.
    """
    # What actually reaches stdout is asserted against the running script in
    # tests/scripts/test_memory_sampler.py; this only pins the workflow side,
    # where the sampler steps must stay wired and the artifact upload must stay
    # failure-only rather than becoming the sole channel.
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        by_name = {step.get("name"): step for step in workflow["jobs"][job_id]["steps"]}
        assert_that(by_name["Upload memory diagnostics"]["if"]).described_as(
            job_id,
        ).is_equal_to("failure()")
        assert_that(by_name["Stop memory sampler"]["if"]).described_as(
            job_id,
        ).is_equal_to("always()")


def test_build_binaries_and_dist_build_ahead_of_the_gate() -> None:
    """The binary build hangs off classify-tag and the dist keeps 90 days."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    assert_that(publish["jobs"]["build-binaries"]["needs"]).is_equal_to(
        ["classify-tag"],
    )
    assert_that(
        publish["jobs"]["pypi-build"]["with"]["artifact-retention-days"],
    ).is_equal_to(90)


def test_homebrew_dispatch_reads_the_arm64_digest_from_the_manifest() -> None:
    """The tap gets the digest the gate verified, after the release exists."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    tap = publish["jobs"]["homebrew-tap"]
    assert_that(set(tap["needs"])).is_equal_to(
        {"classify-tag", "release-gate", "github-release"},
    )
    assert_that(tap["permissions"]).is_equal_to({"contents": "read"})
    callee = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    dispatch = callee["jobs"]["homebrew-dispatch"]
    by_name = {step.get("name"): step for step in dispatch["steps"]}
    assert_that(by_name["Download release manifest"]["with"]["name"]).is_equal_to(
        "release-manifest",
    )
    payload = by_name["Dispatch formula update"]["with"]
    assert_that(payload["binary-arm64-sha"]).is_equal_to(
        "${{ steps.checksums.outputs.arm64_sha256 }}",
    )
    assert_that(callee["jobs"]).does_not_contain_key("upload-binaries")
    assert_that(callee["jobs"]).does_not_contain_key("upload-man-page")
    # npm no longer waits on Homebrew: both hang off the release.
    assert_that(_job_ancestors(publish, job_id="npm-publish")).does_not_contain(
        "homebrew-tap",
    )
    assert_that(publish["jobs"]["npm-publish"]["needs"]).contains("github-release")
