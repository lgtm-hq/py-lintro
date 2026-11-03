"""Workflow wiring contracts: publishing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from tests.unit.workflow_wiring_helpers import (
    _BINARY_SIGNER_WORKFLOW,
    _BINARY_WORKFLOWS,
    _NPM_PACKAGE_ORDER,
    _NPM_SET_REUSABLE,
    _OIDC_HOST,
    _PUBLISH_BINARIES_WORKFLOW,
    _REPO_ROOT,
    _SHA_PIN_RE,
    _SIGSTORE_HOSTS,
    _canonical_lgtm_ci_pin,
    _endpoint_set,
    _job_steps,
    _load_workflow,
    _normalize_github_expr,
    _publish_npm_jobs,
)


def test_publish_npm_calls_the_lgtm_ci_package_set_reusable() -> None:
    """The publish job is lgtm-ci's package-set reusable at the canonical pin.

    Everything from "directory of packages" onward moved upstream (#2632):
    the ordered, idempotent publish loop, pre-publish artifact verification
    and post-publish registry verification. The caller must grant the
    callee's static permissions or the run startup-fails (#2484 class), and
    both the ``uses:`` ref and ``tooling-ref`` must be the repo-wide pin.
    """
    jobs = _publish_npm_jobs()
    publish = jobs["publish"]
    canonical = _canonical_lgtm_ci_pin()
    assert_that(publish["uses"]).is_equal_to(f"{_NPM_SET_REUSABLE}@{canonical}")
    assert_that(publish["with"]["tooling-ref"]).is_equal_to(canonical)
    assert_that(publish["permissions"]).is_equal_to(
        {"contents": "read", "id-token": "write", "attestations": "write"},
    )
    needs = publish["needs"]
    assert_that([needs] if isinstance(needs, str) else needs).is_equal_to(["stage"])

    # The tag pipeline's caller job must in turn grant what the nested
    # reusable and the attesting stage job request.
    tag_pipeline = _load_workflow(name="publish-pypi-on-tag.yml")
    caller = tag_pipeline["jobs"]["npm-publish"]
    assert_that(caller["uses"]).is_equal_to("./.github/workflows/publish-npm.yml")
    assert_that(caller["permissions"]).is_equal_to(
        {"contents": "read", "id-token": "write", "attestations": "write"},
    )


def test_publish_npm_binds_the_environment_approval_to_the_publish_job() -> None:
    """The npm approval gate sits on the job that publishes, via the reusable.

    A ``uses:`` job cannot declare ``environment``; lgtm-ci 0.74.0's
    package-set reusable takes it as an input and binds its publish job, so
    the approval and the OIDC environment claim stay where the token is
    minted and the npmjs trusted-publisher registration keeps its ``npm``
    environment binding (#2632, lgtm-hq/lgtm-ci#990). ``stage`` carries no
    environment: nothing there is irreversible.
    """
    jobs = _load_workflow(name="publish-npm.yml")["jobs"]

    assert_that(jobs["stage"]).does_not_contain_key("environment")
    assert_that(jobs["publish"]).does_not_contain_key("environment")
    assert_that(jobs["publish"]["with"]["environment"]).is_equal_to("npm")
    assert_that(jobs["publish"]["needs"]).is_equal_to("stage")


def test_publish_npm_reusable_verifies_artifacts_before_packing() -> None:
    """verify-artifacts is wired: manifest, signer, order, handoff, smoke.

    The reusable fails a live publish closed without ``checksums-file``,
    ``signer-repo`` and ``signer-workflow``; a tampered or unattested file
    then fails before ``npm pack`` (covered by the reusable's own tests).
    The signer is this workflow file: the stage job attests the staged set,
    and a called workflow signs as its own file, not as the entry workflow.
    """
    with_block = _publish_npm_jobs()["publish"]["with"]
    assert_that(with_block["packages-dir"]).is_equal_to("npm")
    assert_that(with_block["artifact-name"]).is_equal_to("npm-dist")
    assert_that(json.loads(with_block["order"])).is_equal_to(_NPM_PACKAGE_ORDER)
    assert_that(with_block["checksums-file"]).is_equal_to("npm/SHA256SUMS")
    assert_that(with_block["signer-repo"]).is_equal_to("lgtm-hq/py-lintro")
    assert_that(with_block["signer-workflow"]).is_equal_to(
        ".github/workflows/publish-npm.yml",
    )
    assert_that(with_block["post-publish-verify"]).is_true()
    assert_that(with_block["smoke-command"]).is_equal_to(
        "./node_modules/.bin/lintro --version",
    )
    assert_that(with_block["provenance"]).is_true()
    assert_that(with_block["access"]).is_equal_to("public")
    assert_that(str(with_block["dry-run"])).contains("inputs.dry_run")


def test_publish_npm_exposes_dist_tag_for_backfills() -> None:
    """publish-npm accepts dist_tag and forwards it to the reusable."""
    workflow = _load_workflow(name="publish-npm.yml")
    on = workflow["on"]
    assert_that(on["workflow_call"]["inputs"]["dist_tag"]["default"]).is_equal_to(
        "latest",
    )
    assert_that(on["workflow_dispatch"]["inputs"]["dist_tag"]["default"]).is_equal_to(
        "latest",
    )
    # A dispatch is a dry-run unless someone opts in; a call is live.
    assert_that(on["workflow_dispatch"]["inputs"]["dry_run"]["default"]).is_true()
    assert_that(on["workflow_call"]["inputs"]["dry_run"]["default"]).is_false()
    dist_tag = str(workflow["jobs"]["publish"]["with"]["dist-tag"])
    assert_that(dist_tag).contains("inputs.dist_tag")


def test_publish_npm_entry_guard_names_the_workflow_that_calls_it() -> None:
    """Live runs are bound to the one entry workflow npm trusts (#2247).

    npm trusted publishing matches the run's *entry* workflow file, so the
    reusable's guard must allowlist exactly the tag pipeline for live runs,
    and that file must exist and really call publish-npm.yml. A dry-run
    dispatch of publish-npm.yml leaves the allowlist empty so the packaging
    steps can still be rehearsed without publishing.
    """
    entry = str(_publish_npm_jobs()["publish"]["with"]["entry-workflows"])
    match = re.search(r"'(\.github/workflows/[^']+\.yml)'", entry)
    assert_that(match).described_as(entry).is_not_none()
    assert match is not None  # narrow type for mypy
    allowlisted = match.group(1)
    assert_that(allowlisted).is_equal_to(".github/workflows/publish-pypi-on-tag.yml")
    assert_that(entry).contains("!inputs.dry_run")
    assert_that(entry.strip()).ends_with("|| '' }}")

    entry_workflow = _load_workflow(name=Path(allowlisted).name)
    callers = [
        job
        for job in entry_workflow["jobs"].values()
        if isinstance(job, dict)
        and str(job.get("uses", "")).endswith("publish-npm.yml")
    ]
    assert_that(callers).described_as(
        f"{allowlisted} must call publish-npm.yml",
    ).is_not_empty()


def test_publish_npm_stage_verifies_release_binaries_before_staging() -> None:
    """The stage job checks digests and attestations, then attests the set.

    Order matters: download (binaries + SHA256SUMS) → verify each binary
    against the manifest and its build-binaries.yml attestation → stage →
    version → smoke → write the package-set manifest → attest it → upload.
    The attestation's subjects are exactly the manifest the reusable reads,
    so the bytes it packs are the bytes this job verified and signed.
    """
    steps = _publish_npm_jobs()["stage"]["steps"]
    runs = [str(step.get("run", "")).strip() for step in steps]
    uses = [str(step.get("uses", "")) for step in steps]

    def index_of(needle: str, haystack: list[str]) -> int:
        hits = [i for i, entry in enumerate(haystack) if needle in entry]
        assert_that(hits).described_as(needle).is_length(1)
        return hits[0]

    download = index_of("scripts/ci/npm/download_release_binaries.sh", runs)
    verify = index_of("scripts/ci/npm/verify_release_binaries.sh", runs)
    stage = index_of("scripts/ci/npm/stage_binaries.py", runs)
    checksums = index_of("scripts/ci/npm/write_package_checksums.py", runs)
    attest = index_of("actions/attest-build-provenance@", uses)
    upload = index_of("actions/upload-artifact@", uses)
    smoke = index_of("scripts/ci/npm/smoke_test.sh", runs)
    # The smoke test executes a release binary, so it runs LAST: after the
    # manifest is attested and the artifact uploaded, nothing it could
    # rewrite in the workspace is what gets published (Codex review on
    # #2667). It must still be a step of this job so a failure blocks the
    # publish job.
    assert_that(
        [download, verify, stage, checksums, attest, upload, smoke],
    ).is_sorted()
    assert_that(smoke).is_equal_to(len(steps) - 1)

    verify_step = steps[verify]
    assert_that(verify_step["run"].strip()).is_equal_to(
        "scripts/ci/npm/verify_release_binaries.sh",
    )
    assert_that(verify_step["env"]["ATTESTATION_REPO"]).is_equal_to("lgtm-hq/py-lintro")
    assert_that(verify_step["env"]["BINARY_SIGNER_WORKFLOW"]).is_equal_to(
        _BINARY_SIGNER_WORKFLOW,
    )
    assert_that(runs[checksums]).contains("--output npm/SHA256SUMS")
    assert_that(steps[attest]["with"]).is_equal_to(
        {"subject-checksums": "npm/SHA256SUMS"},
    )
    assert_that(steps[upload]["with"]["name"]).is_equal_to("npm-dist")
    assert_that(steps[upload]["with"]["path"]).is_equal_to("npm/")
    assert_that(steps[upload]["with"]["retention-days"]).is_equal_to(90)


def test_publish_npm_has_no_local_publish_script() -> None:
    """publish_packages.sh and assert_dispatch_allowed.sh are gone (#2632).

    The publish loop and the entry guard live in lgtm-ci now; a local copy
    creeping back would fork the audited loop. No step in publish-npm.yml
    may run ``npm publish`` itself either.
    """
    npm_scripts = _REPO_ROOT / "scripts" / "ci" / "npm"
    on_disk = sorted(child.name for child in npm_scripts.iterdir())
    assert_that(on_disk).does_not_contain(
        "publish_packages.sh",
        "assert_dispatch_allowed.sh",
    )
    assert_that(on_disk).contains(
        "download_release_binaries.sh",
        "verify_release_binaries.sh",
        "stage_binaries.py",
        "sync_npm_version.py",
        "smoke_test.sh",
        "write_package_checksums.py",
    )
    for job_id, job in _publish_npm_jobs().items():
        for step in job.get("steps") or []:
            run = str(step.get("run", ""))
            assert_that(run).described_as(
                f"{job_id}: {step.get('name')}",
            ).does_not_contain(
                "npm publish",
                "publish_packages.sh",
                "assert_dispatch_allowed.sh",
            )


def test_publish_npm_reusable_allowlist_carries_the_publish_hosts() -> None:
    """Replace-mode egress for the reusable names every host it needs.

    Under ``allowed-endpoints-mode: replace`` the list is passed verbatim to
    harden-runner, so it must carry the registry, the OIDC token endpoint,
    the Sigstore hosts (npm provenance), the GitHub API and TUF root (gh
    attestation verify) and the artifact download service.
    """
    with_block = _publish_npm_jobs()["publish"]["with"]
    assert_that(with_block["egress-policy"]).is_equal_to("audit")
    assert_that(with_block["allowed-endpoints-mode"]).is_equal_to("replace")
    endpoints = _endpoint_set(with_block["allowed-endpoints"])
    assert endpoints is not None  # literal list, narrow for mypy
    required = {
        "registry.npmjs.org:443",
        "api.github.com:443",
        "github.com:443",
        "pipelines.actions.githubusercontent.com:443",
        "*.blob.core.windows.net:443",
        _OIDC_HOST,
        *_SIGSTORE_HOSTS,
    }
    assert_that(sorted(required - endpoints)).is_empty()


def test_publish_pypi_top_level_permissions_are_empty() -> None:
    """The tag publisher grants no scopes at the top level (#2511).

    Every job in ``publish-pypi-on-tag.yml`` declares its own ``permissions``
    block, so a top-level grant is dead configuration that only widens the
    default token. The ``actions: read`` the binary build stage needs belongs
    on the ``build-binaries`` caller job (#2440, #2562); the tap dispatch is
    read-only since the release reusable attaches the assets.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    assert_that(publish["permissions"]).is_equal_to({})
    homebrew = publish["jobs"]["homebrew-tap"]["permissions"]
    assert_that(homebrew).is_equal_to({"contents": "read"})
    build = publish["jobs"]["build-binaries"]["permissions"]
    assert_that(build).contains_entry({"actions": "read"})
    assert_that(build).contains_entry({"contents": "read"})


def test_no_testpypi_workflow_or_endpoints_remain() -> None:
    """The dead TestPyPI staging lane stays deleted (#2601).

    ``publish-testpypi.yml`` never ran once, so it was a secret and an
    environment kept alive for nothing. Releases are verified by installing
    from real PyPI. The egress assertion is what stops the lane creeping back
    in as an allowlisted upload host on the production build job.
    """
    workflow_dir = _REPO_ROOT / ".github" / "workflows"
    matches = sorted(path.name for path in workflow_dir.glob("*testpypi*"))
    assert_that(matches).is_empty()

    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    build_with = publish["jobs"]["pypi-build"]["with"]
    allowed = set(build_with["allowed-endpoints"].split())
    assert_that(build_with["allowed-endpoints-mode"]).is_equal_to("replace")
    assert_that(allowed).does_not_contain("test.pypi.org:443")
    assert_that(allowed).does_not_contain("upload.test.pypi.org:443")

    # The upload job carries its own harden-runner allowlist; a staging host
    # must not creep back in there either.
    harden = next(
        step
        for step in publish["jobs"]["pypi-upload"]["steps"]
        if "harden-runner" in str(step.get("uses", ""))
    )
    upload_allowed = set(str(harden["with"]["allowed-endpoints"]).split())
    assert_that(upload_allowed).does_not_contain("test.pypi.org:443")
    assert_that(upload_allowed).does_not_contain("upload.test.pypi.org:443")


def test_publish_pypi_sbom_fails_on_high_severity() -> None:
    """Release SBOM must gate publishes on high/critical vulns (#1118)."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    sbom = publish["jobs"]["sbom"]
    assert_that(sbom["with"]).contains_entry({"fail-on-severity": "high"})
    assert_that(sbom["with"].get("scan-vulnerabilities")).is_true()


@pytest.mark.parametrize(
    "workflow_name",
    ["publish-pypi-on-tag.yml", "sbom-on-main.yml"],
)
def test_sbom_callers_grant_contents_read_only(workflow_name: str) -> None:
    """SBOM callers stay read-only on contents (#1807).

    Generation, vulnerability scan and attestation only read the repository.
    The ``contents: write`` release-asset upload moved into
    ``reusable-sbom-release-upload.yml`` upstream (lgtm-ci#770), so a
    ``contents: write`` grant here is silent over-permissioning on the
    production tag-publish path.
    """
    permissions = _load_workflow(name=workflow_name)["jobs"]["sbom"]["permissions"]
    assert_that(permissions).contains_entry({"contents": "read"})
    # Attestation signing and code-scanning upload still need these.
    assert_that(permissions).contains_entry({"security-events": "write"})
    assert_that(permissions).contains_entry({"id-token": "write"})
    assert_that(permissions).contains_entry({"attestations": "write"})


def test_mirror_release_serializes_with_global_concurrency() -> None:
    """Only one mirror bump runs at a time to avoid clobbering newer pins."""
    workflow = _load_workflow(name="mirror-release.yml")

    assert_that(workflow["concurrency"]["group"]).is_equal_to("mirror-release")
    assert_that(workflow["concurrency"]["cancel-in-progress"]).is_false()


def test_mirror_release_job_has_timeout() -> None:
    """Mirror bump inherits a bounded job timeout instead of the 6-hour default.

    50 minutes covers the configured step budgets (#2742): the ~20-minute
    PyPI wheel wait (30 x (30s curl + 10s sleep)), ~6 minutes of wheel
    verify/download, 1-2 minutes of mint/commit/PR, the bounded 15-minute
    auto-merge poll, and slack — GitHub must not kill the job while it
    polls.
    """
    workflow = _load_workflow(name="mirror-release.yml")

    timeout = workflow["jobs"]["mirror-bump"]["timeout-minutes"]
    assert_that(timeout).is_instance_of(int)
    assert_that(timeout).is_equal_to(50)


def test_mirror_release_is_called_not_release_triggered() -> None:
    """Mirror bump is a reusable call plus a manual dispatch fallback.

    A ``release: published`` trigger is unreachable here: the tag pipeline
    creates the release with GITHUB_TOKEN and GitHub suppresses workflow
    events for actions taken with that token, so the workflow logged zero runs across
    every release since (#2599). ``push: tags`` carries the same recursion
    guard, so ``workflow_call`` is the only trigger the automated train fires.
    """
    workflow = _load_workflow(name="mirror-release.yml")
    triggers = workflow["on"]

    assert_that(triggers).contains_key("workflow_call", "workflow_dispatch")
    assert_that(triggers).does_not_contain_key("release")
    assert_that(triggers).does_not_contain_key("push")
    assert_that(triggers["workflow_call"]["inputs"]).contains_key("release_tag")
    assert_that(
        triggers["workflow_call"]["inputs"]["release_tag"]["required"],
    ).is_true()
    assert_that(triggers["workflow_call"]["secrets"]).contains_key(
        "MIRROR_APP_ID",
        "MIRROR_APP_PRIVATE_KEY",
    )
    assert_that(triggers["workflow_dispatch"]["inputs"]).contains_key("release_tag")
    assert_that(workflow["jobs"]["mirror-bump"]["env"]["RELEASE_TAG"]).is_equal_to(
        "${{ inputs.release_tag }}",
    )


def test_mirror_token_guard_probes_the_secret_into_an_output() -> None:
    """A guard job turns the unreadable secrets into a job output (#2622).

    Secrets cannot be referenced from a job-level ``if``, so the only way to
    gate the mirror call on the mirror App credentials existing is to read
    them into a step env var and re-export the verdict. The job itself needs
    nothing from the repo, hence ``permissions: {}``.
    """
    workflow = _load_workflow(name="publish-pypi-on-tag.yml")
    job = workflow["jobs"]["mirror-token"]

    assert_that(job["permissions"]).is_equal_to({})
    assert_that(job["needs"]).contains("github-release")
    assert_that(job["outputs"]["has_token"]).is_equal_to(
        "${{ steps.probe.outputs.has_token }}",
    )

    step = next(s for s in _job_steps(workflow, job="mirror-token") if "run" in s)
    assert_that(step["id"]).is_equal_to("probe")
    assert_that(step["env"]["APP_ID"]).is_equal_to(
        "${{ secrets.MIRROR_APP_ID }}",
    )
    assert_that(step["env"]["APP_KEY"]).is_equal_to(
        "${{ secrets.MIRROR_APP_PRIVATE_KEY }}",
    )
    run = step["run"]
    assert_that(run).contains("has_token=true")
    assert_that(run).contains("has_token=false")
    assert_that(run).contains("$GITHUB_OUTPUT")
    assert_that(run).contains("$GITHUB_STEP_SUMMARY")


def test_mirror_token_guard_warns_when_the_secret_is_absent() -> None:
    """The skip is loud: an annotation plus a step-summary line (#2622)."""
    workflow = _load_workflow(name="publish-pypi-on-tag.yml")
    step = next(s for s in _job_steps(workflow, job="mirror-token") if "run" in s)
    message_a = "MIRROR_APP_ID / MIRROR_APP_PRIVATE_KEY are not both set;"
    message_b = "lintro-pre-commit mirror bump skipped"

    run = step["run"]
    assert_that(run).contains(f'msg="{message_a}"')
    assert_that(run).contains(f'msg="${{msg}} {message_b}"')
    assert_that(run).contains('echo "::warning::${msg}"')
    assert_that(run).contains('echo "${msg}." >>"$GITHUB_STEP_SUMMARY"')


def test_mirror_release_is_gated_on_the_token_guard() -> None:
    """The mirror call waits for the guard and runs only when it says true.

    Without this the job fails every tag at checkout with "Input required and
    not supplied: token", reddening an otherwise complete release run (#2622).
    The ``actions-v`` recursion guard stays alongside the new condition.
    """
    workflow = _load_workflow(name="publish-pypi-on-tag.yml")
    job = workflow["jobs"]["mirror-release"]

    assert_that(job["needs"]).contains("github-release", "mirror-token")
    assert_that(job["if"]).contains(
        "needs.mirror-token.outputs.has_token == 'true'",
    )
    assert_that(job["if"]).contains("!startsWith(github.ref_name, 'actions-v')")


def test_mirror_release_job_is_read_only_in_source_repo() -> None:
    """Cross-repo writes use the mirror App token; source-repo perms stay minimal."""
    workflow = _load_workflow(name="mirror-release.yml")

    assert_that(workflow["permissions"]).is_equal_to({})
    assert_that(workflow["jobs"]["mirror-bump"]["permissions"]).is_equal_to(
        {"contents": "read", "attestations": "read"},
    )


def test_mirror_release_hardens_runner_with_audited_egress() -> None:
    """First step audits runner egress and retains the allowlist."""
    workflow = _load_workflow(name="mirror-release.yml")
    first = _job_steps(workflow, job="mirror-bump")[0]

    assert_that(first["uses"]).starts_with("step-security/harden-runner@")
    assert_that(first["with"]["egress-policy"]).is_equal_to("audit")
    endpoints = first["with"]["allowed-endpoints"]
    assert_that(endpoints).contains("pypi.org:443")
    assert_that(endpoints).contains("files.pythonhosted.org:443")
    assert_that(endpoints).contains("api.github.com:443")
    # gh release download follows redirects to this host; the attestation
    # bundle store (*.blob.core.windows.net) is enforced by the ratchet test.
    assert_that(endpoints).contains("release-assets.githubusercontent.com:443")


def test_mirror_release_actions_are_sha_pinned() -> None:
    """Every third-party action in the mirror workflow is pinned to a commit SHA."""
    workflow = _load_workflow(name="mirror-release.yml")
    uses = [
        step["uses"]
        for step in _job_steps(workflow, job="mirror-bump")
        if "uses" in step
    ]

    assert_that(uses).is_not_empty()
    for ref in uses:
        assert_that(_SHA_PIN_RE.search(ref)).described_as(ref).is_not_none()


def test_mirror_release_mints_app_token_instead_of_a_pat() -> None:
    """Mirror checkout and publish authenticate with a minted App token (#2742).

    The bump commit must be GitHub-signed and attributed to the
    ``lgtm-mirror-bot`` App account for the mirror's signature and
    unattributed-changes rulesets; the retired plain-PAT secret could
    provide neither. The mint is scoped to the mirror repo explicitly.
    """
    workflow = _load_workflow(name="mirror-release.yml")
    steps = _job_steps(workflow, job="mirror-bump")

    mint = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/create-github-app-token@")
    )
    assert_that(mint["with"]["app-id"]).contains("secrets.MIRROR_APP_ID")
    assert_that(mint["with"]["private-key"]).contains(
        "secrets.MIRROR_APP_PRIVATE_KEY",
    )
    assert_that(mint["with"]["owner"]).is_equal_to("lgtm-hq")
    assert_that(mint["with"]["repositories"]).is_equal_to("lintro-pre-commit")
    # The minted token is scoped to exactly what the bump needs.
    assert_that(mint["with"]["permission-contents"]).is_equal_to("write")
    assert_that(mint["with"]["permission-pull-requests"]).is_equal_to("write")

    checkout = next(
        step
        for step in steps
        if step.get("with", {}).get("repository") == "lgtm-hq/lintro-pre-commit"
    )
    assert_that(checkout["with"]["token"]).contains("steps.mirror-app.outputs.token")

    publish = next(
        step for step in steps if "publish-mirror-release.sh" in step.get("run", "")
    )
    assert_that(publish["env"]["GH_TOKEN"]).contains("steps.mirror-app.outputs.token")


def test_mirror_release_verifies_the_wheel_before_the_bump() -> None:
    """The wheel is proven against the release gate before any mirror write (#2742).

    wait-for-pypi-wheel.sh only proves existence; the verify step compares
    PyPI's sha256 digest against the release's SHA256SUMS and checks the
    build attestation, both before the App token is even minted.
    """
    workflow = _load_workflow(name="mirror-release.yml")
    steps = _job_steps(workflow, job="mirror-bump")

    def step_order(step: dict[str, Any]) -> int:
        return steps.index(step)

    verify = next(
        step for step in steps if "verify_release_wheel.sh" in step.get("run", "")
    )
    mint = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/create-github-app-token@")
    )
    publish = next(
        step for step in steps if "publish-mirror-release.sh" in step.get("run", "")
    )
    assert_that(step_order(verify)).is_less_than(step_order(mint))
    assert_that(step_order(mint)).is_less_than(step_order(publish))

    assert_that(verify["env"]["ATTESTATION_REPO"]).is_equal_to("lgtm-hq/py-lintro")
    assert_that(verify["env"]["DIST_SIGNER_WORKFLOW"]).contains(
        "reusable-build-python-dist.yml",
    )


def test_mirror_release_skips_prereleases() -> None:
    """Wheel-dependent steps are guarded on stable, non-prerelease releases."""
    workflow = _load_workflow(name="mirror-release.yml")
    steps = _job_steps(workflow, job="mirror-bump")
    guard = "steps.resolve.outputs.is_prerelease == 'false'"

    for needle in (
        "wait-for-pypi-wheel.sh",
        "verify_release_wheel.sh",
        "publish-mirror-release.sh",
    ):
        step = next(s for s in steps if needle in s.get("run", ""))
        assert_that(step["if"]).contains(guard)
        # The tag itself is the only prerelease signal on the call path: there
        # is no release event payload to read `prerelease` from (#2599).
        assert_that(step["if"]).does_not_contain("github.event.release")

    # wait-for-pypi-wheel and publish take the version as an argument; the
    # verify step reads it from the VERSION env var its script requires.
    verify = next(s for s in steps if "verify_release_wheel.sh" in s.get("run", ""))
    assert_that(verify["env"]["VERSION"]).contains("steps.resolve.outputs.version")

    mirror_checkout = next(
        s
        for s in steps
        if s.get("with", {}).get("repository") == "lgtm-hq/lintro-pre-commit"
    )
    assert_that(mirror_checkout["if"]).contains(guard)
    assert_that(mirror_checkout["if"]).does_not_contain("github.event.release")

    setup_python = [
        s for s in steps if s.get("uses", "").startswith("actions/setup-python@")
    ]
    assert_that(setup_python).is_empty()


def test_mirror_release_scripts_are_executable() -> None:
    """The CI scripts referenced by the mirror workflow exist and are executable."""
    scripts = (
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "resolve-version.sh",
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "wait-for-pypi-wheel.sh",
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "verify_release_wheel.sh",
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "publish-mirror-release.sh",
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "bump_pin.py",
    )
    for script in scripts:
        assert_that(script.exists()).described_as(str(script)).is_true()
        assert_that(script.stat().st_mode & 0o111).described_as(
            f"{script} is not executable",
        ).is_not_zero()


def test_publish_binaries_receives_one_named_secret() -> None:
    """The publish call passes exactly the tap dispatch token, not ``inherit``.

    ``secrets: inherit`` would hand the call every org/repo secret; the callee
    needs one, declares it on its ``workflow_call`` interface, and uses no
    other secret besides ``GITHUB_TOKEN`` (#2562 review).
    """
    caller = _load_workflow(name="publish-pypi-on-tag.yml")
    job = caller["jobs"]["homebrew-tap"]
    # Built from parts: the value is a GitHub expression, not a credential,
    # and assembling it keeps the mapping literal out of bandit's B105 net.
    tap_dispatch_key = "HOMEBREW_TAP_DISPATCH_TOKEN"
    expected_expression = "${{ secrets." + tap_dispatch_key + " }}"
    assert_that(job["secrets"]).is_equal_to({tap_dispatch_key: expected_expression})
    build_job = caller["jobs"]["build-binaries"]
    assert_that(build_job).does_not_contain_key("secrets")

    callee = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    declared = callee["on"]["workflow_call"]["secrets"]
    assert_that(set(declared)).is_equal_to({tap_dispatch_key})
    assert_that(declared[tap_dispatch_key]["required"]).is_true()
    for workflow_name in _BINARY_WORKFLOWS:
        text = (_REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8",
        )
        used = set(re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", text))
        assert_that(used - {"GITHUB_TOKEN"}).described_as(workflow_name).is_subset_of(
            set(declared),
        )


@pytest.mark.parametrize("job_name", ["mirror-token", "mirror-release"])
def test_mirror_lane_runs_for_stable_releases_only(job_name: str) -> None:
    """The pre-commit mirror bump must skip prerelease tags explicitly.

    The mirror pins the wheel that pre-commit consumers install, so a
    checkpoint prerelease must never bump it. The v0.160.3a4 checkpoint ran
    the lane (harmlessly, the script pushed nothing); both jobs now gate on
    the classifier like Homebrew and npm, keeping the ``actions-v`` recursion
    guard and, for the bump itself, the token guard (#2562).
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    job = publish["jobs"][job_name]
    condition = _normalize_github_expr(str(job["if"]))

    assert_that(job["needs"]).contains("classify-tag")
    assert_that(condition).contains(
        "needs.classify-tag.outputs.is_prerelease == 'false'",
    )
    assert_that(condition).contains("!startsWith(github.ref_name, 'actions-v')")
    if job_name == "mirror-release":
        assert_that(condition).contains(
            "needs.mirror-token.outputs.has_token == 'true'",
        )
