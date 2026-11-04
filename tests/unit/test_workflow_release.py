"""Workflow wiring contracts: release."""

from __future__ import annotations

import json
import os

import pytest
from assertpy import assert_that

from tests.unit.workflow_wiring_helpers import (
    _BINARY_SIGNER_WORKFLOW,
    _BUILD_STAGE_JOBS,
    _DIST_SIGNER_WORKFLOW,
    _NOTIFIER_REUSABLE,
    _PUBLISH_JOBS,
    _RELEASE_FAULT_SCRIPT,
    _RELEASE_FAULT_VAR,
    _REPO_ROOT,
    _SKEW_SCRIPT,
    _SKEW_WORKFLOW,
    _TAG_PIPELINE_CALLEES,
    _VALIDATION_CHANNELS_VAR,
    _VARS_REFERENCE_RE,
    _canonical_lgtm_ci_pin,
    _evaluate_github_if,
    _evaluate_tag_gate,
    _job_ancestors,
    _job_steps,
    _load_workflow,
    _module_constant,
    _normalize_github_expr,
    _run_classifier,
    _run_fault_step,
    _workflow_paths,
)


def test_release_workflows_use_paired_egress_presets() -> None:
    """Auto-tag and version-pr workflows must use opposite egress presets."""
    auto_tag = _load_workflow(name="release-auto-tag.yml")
    version_pr = _load_workflow(name="release-version-pr.yml")

    assert_that(auto_tag["jobs"]["auto-tag"]["with"]["egress-preset"]).is_equal_to(
        "github-tooling",
    )
    assert_that(auto_tag["jobs"]["auto-tag"]["permissions"]).is_equal_to(
        {
            "actions": "read",
            "contents": "write",
            "issues": "write",
        },
    )
    assert_that(version_pr["jobs"]["version-pr"]["with"]["egress-preset"]).is_equal_to(
        "pypi",
    )
    assert_that(version_pr["jobs"]["version-pr"]["permissions"]).is_equal_to(
        {
            "actions": "read",
            "contents": "write",
            "issues": "write",
            "pull-requests": "write",
        },
    )


def test_auto_tag_dispatch_passes_an_optional_version_override() -> None:
    """A missing release tag can be recovered without reparsing its squash title."""
    workflow = _load_workflow(name="release-auto-tag.yml")
    version_input = workflow["on"]["workflow_dispatch"]["inputs"]["version"]

    assert_that(version_input["type"]).is_equal_to("string")
    assert_that(version_input["required"]).is_false()
    assert_that(version_input["default"]).is_equal_to("")
    assert_that(workflow["jobs"]["auto-tag"]["with"]["version"]).is_equal_to(
        "${{ inputs.version || '' }}",
    )


def test_auto_tag_push_preserves_commit_version_detection() -> None:
    """Main pushes still reach the reusable's default commit-title detection."""
    workflow = _load_workflow(name="release-auto-tag.yml")
    auto_tag = workflow["jobs"]["auto-tag"]

    assert_that(workflow["on"]["push"]).is_equal_to({"branches": ["main"]})
    assert_that(auto_tag).does_not_contain_key("if")
    assert_that(auto_tag["uses"]).starts_with(
        "lgtm-hq/lgtm-ci/.github/workflows/reusable-release-auto-tag.yml@",
    )
    assert_that(auto_tag["with"].get("version-source", "commit")).is_equal_to("commit")
    assert_that(auto_tag["with"]["create-release"]).is_false()


def test_version_pr_is_gated_on_a_green_tag_publish() -> None:
    """The version PR waits on the publish gate (#2516).

    A broken tag publish used to mint one dead version per merge to ``main``
    (v0.151.2 through v0.152.6). The ``publish-gate`` job reads the last
    version-tag publish run and the version-PR job runs only when it was
    green; ``force`` is the manual override for the first release after a fix.
    """
    workflow = _load_workflow(name="release-version-pr.yml")
    gate = workflow["jobs"]["publish-gate"]
    version_pr = workflow["jobs"]["version-pr"]

    assert_that(version_pr["needs"]).contains("publish-gate")
    # Fail open on a *missing* verdict: if the gate job dies before its script
    # writes the output, `== 'true'` would freeze every release. Only an
    # explicit `false` stops the version PR.
    assert_that(_normalize_github_expr(version_pr["if"])).contains(
        "always() && needs.publish-gate.outputs.publish_green != 'false'",
    )
    # Read-only: the gate inspects run conclusions and touches nothing else.
    assert_that(gate["permissions"]).is_equal_to({"actions": "read"})
    assert_that(gate["outputs"]["publish_green"]).contains(
        "steps.gate.outputs.publish_green",
    )
    gate_script = "scripts/ci/check-last-publish-green.py"
    assert_that((_REPO_ROOT / gate_script).is_file()).is_true()
    gate_steps = [
        step for step in gate["steps"] if gate_script in str(step.get("run", ""))
    ]
    assert_that(gate_steps).is_length(1)

    force_input = workflow["on"]["workflow_dispatch"]["inputs"]["force"]
    assert_that(force_input["type"]).is_equal_to("boolean")
    assert_that(force_input["default"]).is_false()

    # The force decision stays in the workflow expression and reaches the
    # script as one quoted word, so no conditional logic lives in inline
    # shell and an unset input cannot smuggle a second argument through.
    gate_step = gate_steps[0]
    force_flag = _normalize_github_expr(str(gate_step["env"]["FORCE_FLAG"]))
    assert_that(force_flag).is_equal_to(
        "${{ inputs.force && '--force' || '' }}",
    )
    assert_that(_normalize_github_expr(str(gate_step["run"]))).is_equal_to(
        f'python3 {gate_script} "${{FORCE_FLAG}}"',
    )


def test_version_pr_skips_an_unfinished_publish() -> None:
    """The gate's active-publish verdict prevents calling the PR generator."""
    workflow = _load_workflow(name="release-version-pr.yml")
    condition = str(workflow["jobs"]["version-pr"]["if"])
    assert_that(
        _evaluate_github_if(
            condition,
            cancelled=False,
            results={"publish-gate": "success"},
            outputs={"publish-gate": {"publish_green": "false"}},
            context_conditions={
                "github.event_name != 'workflow_run'": True,
                "github.event.workflow_run.event == 'push'": False,
                "github.event.workflow_run.head_repository.full_name "
                "== github.repository": False,
            },
        ),
    ).is_false()


def test_version_pr_resumes_after_tag_publication_completes() -> None:
    """Publication completion starts fresh generation without another push."""
    workflow = _load_workflow(name="release-version-pr.yml")
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    trigger = workflow["on"].get("workflow_run", {})

    assert_that(trigger.get("workflows", [])).is_equal_to([publish["name"]])
    assert_that(trigger.get("types", [])).is_equal_to(["completed"])
    # Tag runs carry the tag as head_branch; a main branch filter loses them.
    assert_that(trigger).does_not_contain_key("branches")
    assert_that(trigger).does_not_contain_key("branches-ignore")
    # workflow_run supplies current default-branch github.sha, not the completed
    # publication's old head_sha. Keep the gate checkout on that default too.
    checkout = next(
        step
        for step in workflow["jobs"]["publish-gate"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert_that(checkout["with"]).does_not_contain_key("ref")
    assert_that(workflow["jobs"]["version-pr"]["with"]).does_not_contain_key("ref")


@pytest.mark.parametrize(
    ("event_name", "source_event", "same_repository", "allowed"),
    [
        ("workflow_run", "push", True, True),
        ("workflow_run", "push", False, False),
        ("workflow_run", "workflow_dispatch", True, False),
        ("push", "", False, True),
        ("workflow_dispatch", "", False, True),
    ],
)
@pytest.mark.parametrize("publish_green", ["true", "false", ""])
def test_version_pr_completion_checks_provenance_and_latest_gate(
    event_name: str,
    source_event: str,
    same_repository: bool,
    allowed: bool,
    publish_green: str,
) -> None:
    """Completion cannot bypass the gate or trust checks, even on runner loss."""
    workflow = _load_workflow(name="release-version-pr.yml")
    condition = str(workflow["jobs"]["version-pr"]["if"])
    assert_that(
        _evaluate_github_if(
            condition,
            cancelled=False,
            results={"publish-gate": "success" if publish_green else "failure"},
            outputs={"publish-gate": {"publish_green": publish_green}},
            context_conditions={
                "github.event_name != 'workflow_run'": event_name != "workflow_run",
                "github.event.workflow_run.event == 'push'": source_event == "push",
                "github.event.workflow_run.head_repository.full_name "
                "== github.repository": same_repository,
            },
        ),
    ).is_equal_to(allowed and publish_green != "false")


def test_release_helpers_name_the_real_publish_workflow_file() -> None:
    """Both release helpers must name the live publish workflow file.

    ``publish_green``/skew both hinge on a workflow *file name* passed to the
    Actions API, which answers an empty run list for an unknown file rather
    than erroring. Renaming the workflow would therefore turn both checks into
    permanent, silent all-clears. Resolve the file from its ``name:`` so the
    rename breaks a test instead.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    matches = [
        path.name
        for path in _workflow_paths()
        if _load_workflow(name=path.name).get("name") == "Publish - PyPI Production"
    ]
    assert_that(matches).described_as(
        "exactly one workflow is named 'Publish - PyPI Production'",
    ).is_length(1)
    publish_workflow = matches[0]
    assert_that((workflows_dir / publish_workflow).is_file()).is_true()

    scripts_dir = _REPO_ROOT / "scripts" / "ci"
    for script_name, constant in (
        ("check-last-publish-green.py", "DEFAULT_WORKFLOW"),
        ("check-release-version-skew.py", "DEFAULT_RELEASE_WORKFLOW"),
    ):
        value = _module_constant(
            script=scripts_dir / script_name,
            name=constant,
        )
        assert_that(value).described_as(
            f"{script_name}:{constant} must name the publish workflow file",
        ).is_equal_to(publish_workflow)


def test_version_pr_finalizes_docs_via_dedicated_script() -> None:
    """Version-PR workflow finalizes CHANGELOG and SECURITY.md via a repo script."""
    version_pr = _load_workflow(name="release-version-pr.yml")

    script = version_pr["jobs"]["version-pr"]["with"]["version-update-script"]
    assert_that(script).is_equal_to("scripts/ci/finalize-version-pr.py")
    assert_that((_REPO_ROOT / script).is_file()).is_true()
    # The finalizer orchestrates the changelog and security-table scripts.
    assert_that((_REPO_ROOT / "scripts/ci/format-changelog.py").is_file()).is_true()
    assert_that(
        (_REPO_ROOT / "scripts/ci/update-security-support.py").is_file(),
    ).is_true()


def test_changelog_no_longer_ignored_by_lintro() -> None:
    """CHANGELOG.md must be linted like every other file (#1117)."""
    ignore = (_REPO_ROOT / ".lintro-ignore").read_text(encoding="utf-8")
    entries = {
        line.strip()
        for line in ignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert_that(entries).does_not_contain("CHANGELOG.md")


@pytest.mark.parametrize(
    ("workflow_name", "identity"),
    [
        ("release-auto-tag.yml", "Release - Auto Tag"),
        ("release-version-pr.yml", "Release - Version PR"),
    ],
)
def test_release_workflows_define_traceable_run_names(
    *,
    workflow_name: str,
    identity: str,
) -> None:
    """Release callers set a run-name carrying workflow identity, event, and branch.

    A dynamic ``run-name`` surfaces post-merge release failures in the Actions
    history (event + branch) instead of the default commit subject, which can
    look healthy even when a release job fails.
    """
    workflow = _load_workflow(name=workflow_name)
    run_name = workflow["run-name"]

    assert_that(run_name).is_instance_of(str)
    assert_that(run_name).contains(identity)
    assert_that(run_name).contains("${{ github.event_name }}")
    assert_that(run_name).contains("${{ github.ref_name }}")


def test_release_workflows_grant_failure_reporting_permissions() -> None:
    """Both release callers grant the upstream report-release-failure job access.

    The lgtm-ci reusables open/update a deduplicated failure issue on ``main``
    release failures, which requires reading workflow/run metadata and writing
    issues. Guarding the permissions keeps that visibility path wired.
    """
    for workflow_name, job_name in (
        ("release-auto-tag.yml", "auto-tag"),
        ("release-version-pr.yml", "version-pr"),
    ):
        permissions = _load_workflow(name=workflow_name)["jobs"][job_name][
            "permissions"
        ]
        assert_that(permissions).contains_entry({"actions": "read"})
        assert_that(permissions).contains_entry({"issues": "write"})


def test_semantic_pr_title_enforces_a_merge_safe_length_limit() -> None:
    """The title check must cap titles so the squash commit passes commitlint.

    commitlint on main enforces a 100-character header and the squash merge
    appends `` (#NNNN)`` to the PR title, so the reusable's ``max-length``
    input must be set and leave at least that suffix's room (#2553).
    """
    workflow = _load_workflow(name="semantic-pr-title.yml")
    with_block = workflow["jobs"]["semantic-title"]["with"]
    assert_that(with_block).contains_key("max-length")
    limit = int(str(with_block["max-length"]))
    assert_that(limit).is_greater_than(0)
    assert_that(limit).is_less_than_or_equal_to(92)


def test_release_version_pr_does_not_arm_auto_merge() -> None:
    """The version-PR workflow must not arm auto-merge on the PRs it opens.

    A version PR is merged deliberately once its generated ``CHANGELOG.md``
    has been read; with ``auto-merge`` on, the reusable squashes every bump,
    minor included, about 14 minutes after opening (#2694).
    """
    workflow = _load_workflow(name="release-version-pr.yml")
    with_block = workflow["jobs"]["version-pr"]["with"]
    assert_that(with_block).contains_key("auto-merge")
    assert_that(with_block["auto-merge"]).is_false()


def test_semantic_pr_title_can_write_failure_comments() -> None:
    """Semantic PR title workflow can upsert failure comments on PRs."""
    workflow = _load_workflow(name="semantic-pr-title.yml")

    assert_that(workflow["jobs"]["semantic-title"]["permissions"]).is_equal_to(
        {
            "contents": "read",
            "pull-requests": "write",
        },
    )


def test_version_skew_audit_runs_on_schedule_and_dispatch() -> None:
    """The skew audit is a scheduled backstop, also runnable on demand.

    Propagation lag means the settle window matters more than immediacy, so
    the alarm runs as a periodic audit rather than inline in the release run.
    """
    workflow = _load_workflow(name=_SKEW_WORKFLOW)
    triggers = workflow["on"]
    assert_that(triggers).contains_key("schedule")
    assert_that(triggers).contains_key("workflow_dispatch")
    assert_that(triggers["schedule"]).is_not_empty()
    assert_that(workflow["permissions"]).is_equal_to({})


def test_version_skew_audit_invokes_the_checked_in_script() -> None:
    """The audit job calls the dedicated script, not inline shell logic."""
    workflow = _load_workflow(name=_SKEW_WORKFLOW)
    steps = workflow["jobs"]["audit"]["steps"]
    run_steps = [step for step in steps if "run" in step]
    assert_that(run_steps).is_length(1)
    assert_that(run_steps[0]["run"]).contains(_SKEW_SCRIPT)
    assert_that((_REPO_ROOT / _SKEW_SCRIPT).exists()).is_true()
    # Alarm, not gate: the audit must not be wired into any release job's
    # ``needs:`` chain, and must not carry write permissions.
    assert_that(workflow["jobs"]["audit"]["permissions"]).is_equal_to(
        {"contents": "read", "actions": "read"},
    )


def test_version_skew_audit_notifies_via_deduplicated_notifier() -> None:
    """Skew alarms ping one deduplicated issue instead of one issue per run."""
    workflow = _load_workflow(name=_SKEW_WORKFLOW)
    notify = workflow["jobs"]["notify-failure"]
    assert_that(notify["needs"]).contains("audit")
    assert_that(notify["uses"]).contains("reusable-main-failure-notifier.yml")
    assert_that(notify["with"]["workflow-key"]).is_equal_to("release-version-skew")
    # Main-only: a dispatch from a feature branch must not open the issue.
    assert_that(_normalize_github_expr(notify["if"])).contains(
        "github.ref == 'refs/heads/main'",
    )


def test_version_skew_audit_allows_every_channel_endpoint() -> None:
    """Egress policy allows exactly the hosts the audit must reach."""
    workflow = _load_workflow(name=_SKEW_WORKFLOW)
    steps = workflow["jobs"]["audit"]["steps"]
    harden = next(
        step for step in steps if str(step.get("uses", "")).startswith("step-security/")
    )
    assert_that(harden["with"]["egress-policy"]).is_equal_to("audit")
    allowed = harden["with"]["allowed-endpoints"].split()
    for endpoint in (
        "pypi.org:443",
        "registry.npmjs.org:443",
        "raw.githubusercontent.com:443",
        "api.github.com:443",
    ):
        assert_that(allowed).contains(endpoint)


def test_main_promotion_attests_the_promoted_digests() -> None:
    """``ghcr.io/lgtm-hq/py-lintro:main`` carries a GitHub attestation (#2630).

    The ``publish`` job promotes ``ci-<run_id>`` digests to ``main``/``sha-*``
    and cosign-signs them; without an attestation step the rolling tags had a
    signature and nothing else. The pushed ``ci-*`` builds must also attach
    BuildKit provenance and an SBOM, because promotion by digest keeps
    exactly what the build attached.
    """
    ci = _load_workflow(name="docker-ci.yml")

    build_steps = ci["jobs"]["docker-build"]["steps"]
    pushed = [
        step
        for step in build_steps
        if "build-push-action" in str(step.get("uses", ""))
        and (step.get("with") or {}).get("push") is True
    ]
    assert_that(pushed).is_length(2)
    for step in pushed:
        with_block = step["with"]
        assert_that(str(with_block.get("provenance"))).described_as(
            step["name"],
        ).is_equal_to("mode=max")
        assert_that(with_block.get("sbom")).described_as(step["name"]).is_true()

    publish = ci["jobs"]["publish"]
    assert_that(publish["permissions"]["attestations"]).is_equal_to("write")
    assert_that(publish["permissions"]["id-token"]).is_equal_to("write")
    attest_steps = [
        step
        for step in publish["steps"]
        if "actions/attest-build-provenance@" in str(step.get("uses", ""))
    ]
    subjects = {
        (step["with"]["subject-name"], str(step["with"]["subject-digest"]))
        for step in attest_steps
    }
    assert_that(subjects).is_equal_to(
        {
            ("ghcr.io/lgtm-hq/py-lintro", "${{ steps.promote.outputs.digest }}"),
            (
                "ghcr.io/lgtm-hq/py-lintro-base",
                "${{ steps.promote-base.outputs.digest }}",
            ),
        },
    )
    for step in attest_steps:
        assert_that(step["with"].get("push-to-registry")).is_true()


def test_release_manifest_is_retained_for_the_recovery_window() -> None:
    """The gate's manifest and assets are 90-day artifacts (#2562 PR (c))."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    gate = publish["jobs"]["release-gate"]
    steps = gate["steps"]
    uploads = {
        str(s["with"]["name"]): s["with"]
        for s in steps
        if "actions/upload-artifact@" in str(s.get("uses", ""))
    }
    assert_that(set(uploads)).is_equal_to({"release-assets", "release-manifest"})
    for name, with_block in uploads.items():
        assert_that(with_block["retention-days"]).described_as(name).is_equal_to(90)
        assert_that(with_block["if-no-files-found"]).described_as(name).is_equal_to(
            "error",
        )
    write = next(
        s
        for s in steps
        if s.get("run") == "python3 scripts/ci/release-gate/write_manifest.py"
    )
    assert_that(write["env"]["OUTPUT"]).is_equal_to(uploads["release-manifest"]["path"])
    assert_that(write["env"]["ASSETS_DIR"] + "/").is_equal_to(
        uploads["release-assets"]["path"],
    )
    for var, output in (
        ("BASE_DIGEST", "base-digest"),
        ("FULL_DIGEST", "full-digest"),
        ("AI_DIGEST", "ai-digest"),
    ):
        assert_that(write["env"][var]).is_equal_to(
            f"${{{{ needs.docker-build.outputs.{output} }}}}",
        )
    for script in (
        "scripts/ci/write-release-manifest.py",
        "scripts/ci/release-gate/write_manifest.py",
        "scripts/ci/release-gate/verify_artifacts.sh",
        "scripts/ci/release-gate/read_manifest_sha.sh",
    ):
        assert_that(os.access(_REPO_ROOT / script, os.X_OK)).described_as(
            script,
        ).is_true()


# --- #2562 PR (c): the gate and the reorder ----------------------------------


def test_release_gate_needs_every_build_job_and_verifies_per_signer() -> None:
    """release-gate is fed by every build job and fails closed on each check.

    dist/* was attested inside lgtm-ci's build reusable and the binaries
    inside build-binaries.yml; a reusable signs as the called file, so the
    gate passes per-kind signer workflows and never the entry workflow.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    gate = publish["jobs"]["release-gate"]
    assert_that(set(gate["needs"])).is_equal_to({"classify-tag", *_BUILD_STAGE_JOBS})
    assert_that(gate["permissions"]).is_equal_to(
        {"contents": "read", "attestations": "read"},
    )
    assert_that(_normalize_github_expr(str(gate["if"]))).contains(
        "github.ref_type == 'tag'",
    )
    steps = gate["steps"]
    verify = next(
        s
        for s in steps
        if s.get("run") == "scripts/ci/release-gate/verify_artifacts.sh"
    )
    env = verify["env"]
    assert_that(env["ATTESTATION_REPO"]).is_equal_to("lgtm-hq/py-lintro")
    assert_that(env["DIST_SIGNER_WORKFLOW"]).is_equal_to(_DIST_SIGNER_WORKFLOW)
    assert_that(env["BINARY_SIGNER_WORKFLOW"]).is_equal_to(_BINARY_SIGNER_WORKFLOW)
    assert_that(env).contains_key("GH_TOKEN")
    downloaded = {
        str((s.get("with") or {}).get("name") or (s.get("with") or {}).get("pattern"))
        for s in steps
        if "actions/download-artifact@" in str(s.get("uses", ""))
    }
    assert_that(downloaded).is_equal_to(
        {"python-dist", "lintro-macos-*", "lintro-linux-*", "lintro-man-page"},
    )
    for step in steps:
        if step.get("run") or "upload-artifact" in str(step.get("uses", "")):
            assert_that(step.get("continue-on-error")).described_as(
                str(step.get("name")),
            ).is_none()
            assert_that(step.get("if")).described_as(str(step.get("name"))).is_none()


def test_every_publish_job_is_downstream_of_the_release_gate() -> None:
    """Nothing writes to a channel unless release-gate passed (#2562)."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    for job_id in _PUBLISH_JOBS:
        assert_that(_job_ancestors(publish, job_id=job_id)).described_as(
            job_id,
        ).contains("release-gate")


def test_no_job_has_a_step_after_its_irreversible_step() -> None:
    """The PyPI upload is the last step of its job, after attestation checks.

    Once ``pypa/gh-action-pypi-publish`` succeeds no rerun can reach a later
    step (#2618), so nothing may follow it; the attestation check precedes
    it via ``prepare-pypi-upload`` with ``require-attestation``.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    irreversible = ("pypa/gh-action-pypi-publish@",)
    for job_id, job in publish["jobs"].items():
        steps = job.get("steps") or []
        for index, step in enumerate(steps):
            if any(marker in str(step.get("uses", "")) for marker in irreversible):
                assert_that(index).described_as(job_id).is_equal_to(len(steps) - 1)
    upload = publish["jobs"]["pypi-upload"]
    assert_that(upload["needs"]).is_equal_to(["release-gate"])
    assert_that(upload["environment"]).is_equal_to("pypi")
    assert_that(upload["permissions"]).is_equal_to(
        {"contents": "read", "attestations": "read", "id-token": "write"},
    )
    names = [str(step.get("uses", "")) for step in upload["steps"]]
    assert_that(names[-1]).starts_with("pypa/gh-action-pypi-publish@")
    assert_that(names[-2]).contains("actions/prepare-pypi-upload@")
    prepare = upload["steps"][-2]["with"]
    assert_that(str(prepare["require-attestation"])).is_equal_to("true")
    assert_that(prepare["signer-workflow"]).is_equal_to(_DIST_SIGNER_WORKFLOW)
    assert_that(" ".join(names)).does_not_contain("attest-build-provenance")


def test_github_release_attaches_the_gated_assets_immutably() -> None:
    """The release carries exactly what the gate assembled, never overwritten."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    release = publish["jobs"]["github-release"]
    assert_that(release["needs"]).is_equal_to(["classify-tag", "pypi-upload"])
    with_block = release["with"]
    assert_that(with_block["artifact-name"]).is_equal_to("release-assets")
    assert_that(with_block["artifact-path"]).is_equal_to("release")
    assert_that(with_block["checksums"]).is_true()
    assert_that(with_block["immutable-assets"]).is_true()
    assert_that(with_block).does_not_contain_key("files")
    assert_that(release["permissions"]).is_equal_to({"contents": "write"})


def test_prerelease_tags_run_the_gate_and_skip_every_channel_publish() -> None:
    """A prerelease proves the build stage and the gate, publishing nothing new."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    stable_only = ("docker-promote", "homebrew-tap", "npm-publish")
    for job_id in stable_only:
        condition = _normalize_github_expr(str(publish["jobs"][job_id]["if"]))
        assert_that(condition).described_as(job_id).contains(
            "needs.classify-tag.outputs.is_prerelease == 'false'",
        )
    for job_id in (*_BUILD_STAGE_JOBS, "release-gate", "pypi-upload", "github-release"):
        condition = _normalize_github_expr(str(publish["jobs"][job_id].get("if", "")))
        assert_that(condition).described_as(job_id).does_not_contain("is_prerelease")


def test_github_release_derives_prerelease_from_the_tag_classifier() -> None:
    """A checkpoint tag must be published as a GitHub prerelease.

    The call used to pass a hard-coded ``prerelease: false``, so the
    v0.160.3a4 checkpoint became GitHub's "latest" release until it was
    edited by hand. The flag now comes from the same ``classify-tag`` output
    that gates Homebrew, npm, Docker and the mirror (#2562).
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    job = publish["jobs"]["github-release"]

    assert_that(job["needs"]).contains("classify-tag", "pypi-upload")
    assert_that(_normalize_github_expr(str(job["with"]["prerelease"]))).is_equal_to(
        "${{ needs.classify-tag.outputs.is_prerelease == 'true' }}",
    )


def test_release_recovery_entry_calls_the_reusable_with_the_tag_path_values() -> None:
    """The recovery entry workflow resumes with the ORIGINAL run's artifacts.

    lgtm-ci#966's reusable never rebuilds: it reads the source run's
    artifacts and resumes the missing channels. The caller must name the
    same artifacts, package set and signer the tag path produces, register
    itself as the npm entry workflow, and default to a dry run (#2633).
    """
    recover = _load_workflow(name="release-recover.yml")
    publish_npm = _load_workflow(name="publish-npm.yml")["jobs"]["publish"]["with"]
    stage_steps = _load_workflow(name="publish-npm.yml")["jobs"]["stage"]["steps"]
    tag_pipeline = _load_workflow(name="publish-pypi-on-tag.yml")
    # PyYAML parses a bare `on:` key as boolean True; the file quotes it.
    on = next(value for key, value in recover.items() if key in ("on", True))

    assert_that(list(on)).is_equal_to(["workflow_dispatch"])
    inputs = on["workflow_dispatch"]["inputs"]
    assert_that(inputs["dry-run"]["default"]).is_true()
    assert_that(inputs["tag"]["required"]).is_true()
    assert_that(inputs["source-run-id"]["required"]).is_true()
    assert_that(recover["permissions"]).is_equal_to({})

    job = recover["jobs"]["recover"]
    assert_that(str(job["uses"])).is_equal_to(
        "lgtm-hq/lgtm-ci/.github/workflows/reusable-release-recover.yml@"
        + _canonical_lgtm_ci_pin(),
    )
    assert_that(job["permissions"]).is_equal_to(
        {
            "actions": "read",
            "contents": "write",
            "issues": "write",
            "id-token": "write",
            "attestations": "write",
        },
    )
    with_block = job["with"]
    assert_that(with_block["tooling-ref"]).is_equal_to(_canonical_lgtm_ci_pin())
    assert_that(with_block["source-workflow"]).is_equal_to(
        ".github/workflows/publish-pypi-on-tag.yml",
    )
    assert_that(with_block["dry-run"]).is_equal_to("${{ inputs.dry-run }}")
    assert_that(with_block["egress-policy"]).is_equal_to("audit")
    # The npm resume must see exactly the package set the tag path staged.
    assert_that(with_block["npm-artifact-name"]).is_equal_to(
        publish_npm["artifact-name"],
    )
    assert_that(with_block["npm-order"]).is_equal_to(publish_npm["order"])
    assert_that(with_block["npm-packages-dir"]).is_equal_to(".")
    assert_that(with_block["npm-checksums-file"]).is_equal_to("SHA256SUMS")
    assert_that(with_block["signer-workflow"]).is_equal_to(
        publish_npm["signer-workflow"],
    )
    assert_that(with_block["signer-repo"]).is_equal_to(publish_npm["signer-repo"])
    assert_that(with_block["npm-entry-workflows"]).is_equal_to(
        ".github/workflows/release-recover.yml",
    )
    # The GitHub Release resume reads the gate's assembled artifact, under
    # the name the gate uploads it as and github-release attaches; the
    # recovery record lands on the issue the tag path's notifier keys
    # (#2670 follow-up, #2633).
    gate_uploads = [
        s
        for s in _job_steps(tag_pipeline, job="release-gate")
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert_that(gate_uploads[0]["with"]["name"]).is_equal_to(
        with_block["release-artifact-name"],
    )
    assert_that(with_block["release-artifact-name"]).is_equal_to(
        tag_pipeline["jobs"]["github-release"]["with"]["artifact-name"],
    )
    assert_that(with_block["recovery-issue-workflow-key"]).is_equal_to(
        tag_pipeline["jobs"]["notify-failure"]["with"]["workflow-key"],
    )
    assert_that(with_block["source-workflow"]).is_equal_to(
        ".github/workflows/publish-pypi-on-tag.yml",
    )
    uploads = [
        s
        for s in stage_steps
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert_that(uploads[0]["with"]["name"]).is_equal_to(with_block["npm-artifact-name"])
    assert_that(job).does_not_contain_key("secrets")


def test_release_failure_notifier_runs_after_every_publish_job() -> None:
    """A failed tag run ends in one deduplicated issue, a green one closes it.

    The notifier needs every channel job and runs under ``!cancelled()`` so a
    partial release is reported whatever failed; ``ref_type`` keeps a branch
    dispatch from filing an issue keyed to a branch name. It is the last job
    in the file: nothing may run after the report.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    job = publish["jobs"]["notify-failure"]
    assert_that(str(job["uses"])).starts_with(
        f"lgtm-hq/lgtm-ci/.github/workflows/{_NOTIFIER_REUSABLE}@",
    )
    condition = _normalize_github_expr(str(job["if"]))
    assert_that(condition).starts_with("!cancelled() &&")
    assert_that(condition).contains("github.ref_type == 'tag'")
    assert_that(set(_PUBLISH_JOBS)).is_subset_of(set(job["needs"]))
    assert_that(job["permissions"]).is_equal_to(
        {"actions": "read", "contents": "read", "issues": "write"},
    )
    with_block = job["with"]
    assert_that(with_block["workflow-key"]).is_equal_to("publish-pypi-on-tag")
    assert_that(with_block["tag"]).is_equal_to("${{ github.ref_name }}")
    assert_that(with_block["channels"]).is_equal_to("${{ toJson(needs) }}")
    assert_that(with_block["egress-policy"]).is_equal_to("audit")
    assert_that(with_block["tooling-ref"]).is_equal_to(_canonical_lgtm_ci_pin())
    assert_that(job).does_not_contain_key("secrets")
    assert_that(list(publish["jobs"])[-1]).is_equal_to("notify-failure")


def test_release_failure_notifier_sees_every_channel_result() -> None:
    """Every job downstream of the gate is a row in the notifier's table."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    needs = set(publish["jobs"]["notify-failure"]["needs"])
    every_other_job = {
        job_id for job_id in publish["jobs"] if job_id != "notify-failure"
    }
    channel_jobs = {
        job_id
        for job_id in every_other_job
        if "release-gate" in _job_ancestors(publish, job_id=job_id)
    }
    assert_that(channel_jobs).is_not_empty()
    assert_that(channel_jobs).is_subset_of(needs)
    # Build-stage jobs too: a failed build skips the gate and every
    # publisher, and a table of skipped rows alone reads as a green run.
    assert_that(needs).is_equal_to(every_other_job)


def test_release_failure_notifier_matches_the_auto_rerun_budget() -> None:
    """Suppression is opt-in and must mirror the auto-rerun call exactly.

    The reusable stays quiet on an attempt within ``max-reruns`` whose failed
    job matches an infra signature, so the budget and the extra signatures
    must equal what auto-rerun-on-infra-failure.yml applies to this workflow;
    otherwise one classifier files while the other retries.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    rerun = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    assert_that(set(rerun["on"]["workflow_run"]["workflows"])).contains(publish["name"])
    rerun_inputs = rerun["jobs"]["rerun"]["with"]
    notifier = publish["jobs"]["notify-failure"]["with"]
    assert_that(notifier["max-reruns"]).is_instance_of(int)
    assert_that(int(rerun_inputs["max-reruns"])).is_equal_to(notifier["max-reruns"])
    assert_that(str(notifier["signatures"]).splitlines()).is_equal_to(
        str(rerun_inputs["signatures"]).splitlines(),
    )


def test_release_gate_owns_the_manifest_and_no_docker_manifest_job_remains() -> None:
    """The (b) ``docker-manifest`` job folded into the gate (#2659 review).

    Two uploads under one artifact name in a run collide, so the manifest is
    written exactly once, by release-gate, after every artifact verified.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    assert_that(publish["jobs"]).does_not_contain_key("docker-manifest")
    writers = [
        (job_id, step)
        for job_id, job in publish["jobs"].items()
        for step in (job.get("steps") or [])
        if "write_manifest.py" in str(step.get("run", ""))
        or "write-release-manifest.py" in str(step.get("run", ""))
    ]
    assert_that([job_id for job_id, _ in writers]).is_equal_to(["release-gate"])
    uploads = [
        job_id
        for job_id, job in publish["jobs"].items()
        for step in (job.get("steps") or [])
        if (step.get("with") or {}).get("name") == "release-manifest"
    ]
    assert_that(uploads).is_equal_to(["release-gate"])


def test_security_md_describes_the_gated_release() -> None:
    """SECURITY.md's supply-chain claim matches the pipeline (#2562)."""
    text = (_REPO_ROOT / ".github" / "SECURITY.md").read_text(encoding="utf-8")
    for expected in (
        "release-gate",
        "gh attestation verify",
        ".intoto.jsonl",
        "build-binaries.yml",
        "immutable",
    ):
        assert_that(text).described_as(expected).contains(expected)
    assert_that(text).does_not_contain("build-binary.yml")


def test_only_classify_tag_reads_repository_variables() -> None:
    """Both switches enter the run through classify-tag's env and nowhere else.

    The issue's wiring rule (#2633): read each variable once, pass it down as
    a job output, so no other job in the tag pipeline or any workflow it
    calls reads ``vars.*``; the variables' state is then visible in one
    place of the run.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    readers = {
        job_id
        for job_id, job in publish["jobs"].items()
        if _VARS_REFERENCE_RE.findall(json.dumps(job))
    }
    assert_that(readers).is_equal_to({"classify-tag"})
    classify = next(
        step
        for step in _job_steps(publish, job="classify-tag")
        if step.get("id") == "classify"
    )
    assert_that(classify["run"]).contains("scripts/ci/classify-release-tag.py")
    assert_that(classify["env"][_VALIDATION_CHANNELS_VAR]).is_equal_to(
        f"${{{{ vars.{_VALIDATION_CHANNELS_VAR} }}}}",
    )
    assert_that(classify["env"][_RELEASE_FAULT_VAR]).is_equal_to(
        f"${{{{ vars.{_RELEASE_FAULT_VAR} }}}}",
    )
    var_reads = sorted(
        set(_VARS_REFERENCE_RE.findall(json.dumps(publish["jobs"]["classify-tag"]))),
    )
    assert_that(var_reads).is_equal_to(
        [f"vars.{_RELEASE_FAULT_VAR}", f"vars.{_VALIDATION_CHANNELS_VAR}"],
    )
    for callee in _TAG_PIPELINE_CALLEES:
        # Parsed, not raw: a comment or an input description may mention the
        # rule; only an expression can read a variable.
        assert_that(
            _VARS_REFERENCE_RE.findall(json.dumps(_load_workflow(name=callee)["jobs"])),
        ).described_as(callee).is_empty()
    outputs = publish["jobs"]["classify-tag"]["outputs"]
    for name in ("is_prerelease", "is_rc", "validation_channels", "release_fault"):
        assert_that(outputs[name]).described_as(name).is_equal_to(
            f"${{{{ steps.classify.outputs.{name} }}}}",
        )


def test_validation_switches_are_no_ops_by_default() -> None:
    """With both variables unset every gate behaves exactly as before #2633.

    The classifier is run as the job runs it, with the variables scrubbed,
    and the resulting outputs drive the same ``if:`` expressions the jobs
    carry: an rc skips npm, Docker and Homebrew, a stable tag runs them, and
    neither fault step has a condition that is true for an empty fault.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    for tag in ("v0.160.3rc1", "v0.160.3a1", "v1.2.3"):
        outputs = _run_classifier(tag=tag, env={})
        assert_that(outputs["validation_channels"]).described_as(tag).is_equal_to(
            "false",
        )
        assert_that(outputs["release_fault"]).described_as(tag).is_equal_to("")
        # Stable isolation (Codex P1 on #2672): a leftover RELEASE_FAULT
        # reaches the fault steps for an rc only; every other tag gets the
        # empty output, so both steps stay no-ops.
        leftover = _run_classifier(tag=tag, env={_RELEASE_FAULT_VAR: "fail-build"})
        assert_that(leftover["release_fault"]).described_as(tag).is_equal_to(
            "fail-build" if outputs["is_rc"] == "true" else "",
        )
        expected = outputs["is_prerelease"] == "false"
        for job_id in ("npm-publish", "docker-promote", "homebrew-tap", "mirror-token"):
            runs = _evaluate_tag_gate(
                str(publish["jobs"][job_id]["if"]),
                is_prerelease=outputs["is_prerelease"],
                validation_channels=outputs["validation_channels"],
            )
            assert_that(runs).described_as(f"{tag} {job_id}").is_equal_to(expected)
    # The fault steps themselves, run as the jobs run them with the empty
    # output the classifier just produced, and with the other fault's name.
    for step_run, other in (
        ("fail-build", "fail-publish-npm"),
        ("fail-publish-npm", "fail-build"),
    ):
        for value in ("", other):
            result = _run_fault_step(fault=step_run, release_fault=value)
            assert_that(result.returncode).described_as(
                f"{step_run} with RELEASE_FAULT={value!r}",
            ).is_equal_to(0)
            assert_that(result.stdout).contains("nothing injected")


def test_validation_channels_open_npm_and_docker_for_rc_tags_only() -> None:
    """The switch extends exactly the npm and Docker gates, for an rc only.

    ``validation_channels`` is derived by the classifier as rc AND the
    variable equals ``true``: an alpha, a beta or a stable tag with the
    variable set yields ``false``. With it true, npm-publish and
    docker-promote run while homebrew-tap and the mirror lane stay skipped
    (the tap has no prerelease lane; the mirror pins the wheel pre-commit
    installs).
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    switch = {_VALIDATION_CHANNELS_VAR: "true"}
    for tag, expected in (
        ("v0.160.3rc1", "true"),
        ("v0.160.3a1", "false"),
        ("v0.160.3b1", "false"),
        ("v1.2.3", "false"),
    ):
        outputs = _run_classifier(tag=tag, env=switch)
        assert_that(outputs["validation_channels"]).described_as(tag).is_equal_to(
            expected,
        )
        assert_that(outputs["is_rc"]).described_as(tag).is_equal_to(expected)
    opened = {"npm-publish": True, "docker-promote": True}
    closed = {"homebrew-tap": False, "mirror-token": False, "mirror-release": False}
    for job_id, expected_run in {**opened, **closed}.items():
        runs = _evaluate_tag_gate(
            str(publish["jobs"][job_id]["if"]),
            is_prerelease="true",
            validation_channels="true",
        )
        assert_that(runs).described_as(job_id).is_equal_to(expected_run)
    # The gates still carry the prerelease clause the stable path relies on
    # and the extension is the one output, in the one shape.
    extension = "needs.classify-tag.outputs.validation_channels == 'true'"
    for job_id in opened:
        condition = _normalize_github_expr(str(publish["jobs"][job_id]["if"]))
        assert_that(condition).described_as(job_id).contains(
            f"(needs.classify-tag.outputs.is_prerelease == 'false' || {extension})",
        )
    for job_id in closed:
        condition = _normalize_github_expr(str(publish["jobs"][job_id]["if"]))
        assert_that(condition).described_as(job_id).does_not_contain(
            "validation_channels",
        )
    # github-release still marks the rc as a prerelease under the switch.
    assert_that(publish["jobs"]["github-release"]["with"]["prerelease"]).contains(
        "is_prerelease == 'true'",
    )


def test_validation_channels_pass_the_next_dist_tag_to_npm() -> None:
    """Under the switch npm publishes as ``next``; a stable release keeps latest."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    npm = _load_workflow(name="publish-npm.yml")
    with_block = publish["jobs"]["npm-publish"]["with"]
    assert_that(_normalize_github_expr(str(with_block["dist_tag"]))).is_equal_to(
        "${{ needs.classify-tag.outputs.validation_channels == 'true'"
        " && 'next' || 'latest' }}",
    )
    assert_that(with_block["release_fault"]).is_equal_to(
        "${{ needs.classify-tag.outputs.release_fault }}",
    )
    on = next(value for key, value in npm.items() if key in ("on", True))
    call_inputs = on["workflow_call"]["inputs"]
    assert_that(call_inputs["dist_tag"]["default"]).is_equal_to("latest")
    assert_that(call_inputs["release_fault"]["type"]).is_equal_to("string")
    assert_that(call_inputs["release_fault"]["default"]).is_equal_to("")
    assert_that(call_inputs["release_fault"]["required"]).is_false()
    assert_that(npm["jobs"]["publish"]["with"]["dist-tag"]).is_equal_to(
        "${{ inputs.dist_tag || 'latest' }}",
    )
    # The manual dispatch surface gained no fault input: faults enter the
    # pipeline through the tag path only.
    assert_that(on["workflow_dispatch"]["inputs"]).does_not_contain_key(
        "release_fault",
    )


def test_validation_channels_promote_the_version_tag_only() -> None:
    """Under the switch docker-promote retags ``<version>`` alone.

    ``env.STABLE_TAGS`` (latest, the three semver tags) is the switch
    negated, ``env.RC_TAGS`` (the pep440 version tag) the switch itself, so
    the two sets are disjoint and together cover every entry the metadata
    steps declare (the entries themselves are pinned by
    ``test_docker_promote_promotes_the_full_release_tag_set``).
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    promote = publish["jobs"]["docker-promote"]
    assert_that(promote["env"]["STABLE_TAGS"]).is_equal_to(
        "${{ needs.classify-tag.outputs.validation_channels != 'true' }}",
    )
    assert_that(promote["env"]["RC_TAGS"]).is_equal_to(
        "${{ needs.classify-tag.outputs.validation_channels == 'true' }}",
    )
    for step in _job_steps(publish, job="docker-promote"):
        if "docker/metadata-action@" not in str(step.get("uses", "")):
            continue
        assert_that(str(step["with"]["flavor"]).strip()).described_as(
            step["id"],
        ).is_equal_to("latest=${{ env.STABLE_TAGS }}")
        entries = [
            line.strip()
            for line in str(step["with"]["tags"]).splitlines()
            if line.strip()
        ]
        rc_entries = [e for e in entries if e.endswith("enable=${{ env.RC_TAGS }}")]
        stable_entries = [
            e for e in entries if e.endswith("enable=${{ env.STABLE_TAGS }}")
        ]
        assert_that(len(rc_entries) + len(stable_entries)).described_as(
            step["id"],
        ).is_equal_to(len(entries))
        assert_that(rc_entries).described_as(step["id"]).is_length(1)
        assert_that(rc_entries[0]).contains("type=pep440,pattern={{version}}")
        assert_that(stable_entries).described_as(step["id"]).is_length(3)
        for entry in stable_entries:
            assert_that(entry).does_not_contain("pep440")


def test_fault_steps_exist_with_their_exact_conditions() -> None:
    """Each fault step fails through the one script, on its own name only.

    ``fail-build`` sits in release-gate (the build calls are reusables with
    no caller-side steps; the gate is the last job before any irreversible
    write and the only build-side job here with steps), before every
    download and the verification. ``fail-publish-npm`` is the first step
    of publish-npm.yml's stage job after the runner hardening and the
    checkout that provides the script, before any binary is downloaded.
    Neither carries an ``if:``: the gate's no-conditional-step invariant
    holds, and the condition is the script's exact comparison of
    ``RELEASE_FAULT`` (the classify-tag output, or the pass-through input)
    with the step's name, exercised here both ways.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    npm = _load_workflow(name="publish-npm.yml")
    script = _REPO_ROOT / _RELEASE_FAULT_SCRIPT
    assert_that(script.exists()).is_true()
    assert_that(os.access(script, os.X_OK)).is_true()

    gate_steps = _job_steps(publish, job="release-gate")
    gate_ids = [str(s.get("id", "")) for s in gate_steps]
    build_fault = gate_steps[gate_ids.index("fail-build")]
    assert_that(build_fault).does_not_contain_key("if")
    assert_that(build_fault).does_not_contain_key("continue-on-error")
    assert_that(build_fault["run"]).is_equal_to(f"{_RELEASE_FAULT_SCRIPT} fail-build")
    assert_that(build_fault["env"]["RELEASE_FAULT"]).is_equal_to(
        "${{ needs.classify-tag.outputs.release_fault }}",
    )
    first_download = next(
        i
        for i, s in enumerate(gate_steps)
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
    )
    verify = next(
        i
        for i, s in enumerate(gate_steps)
        if s.get("run") == "scripts/ci/release-gate/verify_artifacts.sh"
    )
    assert_that(gate_ids.index("fail-build")).is_less_than(first_download)
    assert_that(gate_ids.index("fail-build")).is_less_than(verify)
    preamble = {
        str(s.get("uses", "")).split("@")[0]
        for s in gate_steps[: gate_ids.index("fail-build")]
    }
    assert_that(preamble).is_equal_to(
        {"step-security/harden-runner", "actions/checkout"},
    )

    stage_steps = _job_steps(npm, job="stage")
    stage_ids = [str(s.get("id", "")) for s in stage_steps]
    npm_fault = stage_steps[stage_ids.index("fail-publish-npm")]
    assert_that(npm_fault).does_not_contain_key("if")
    assert_that(npm_fault).does_not_contain_key("continue-on-error")
    assert_that(npm_fault["run"]).is_equal_to(
        f"{_RELEASE_FAULT_SCRIPT} fail-publish-npm",
    )
    assert_that(npm_fault["env"]["RELEASE_FAULT"]).is_equal_to(
        "${{ inputs.release_fault }}",
    )
    preamble = {
        str(s.get("uses", "")).split("@")[0]
        for s in stage_steps[: stage_ids.index("fail-publish-npm")]
    }
    assert_that(preamble).is_equal_to(
        {"step-security/harden-runner", "actions/checkout"},
    )
    # No third fault step anywhere: the runbook names exactly these two.
    fault_steps = [
        (name, s.get("id"))
        for name in ("publish-pypi-on-tag.yml", "publish-npm.yml")
        for job in _load_workflow(name=name)["jobs"].values()
        for s in (job.get("steps") or [])
        if _RELEASE_FAULT_SCRIPT in str(s.get("run", ""))
    ]
    assert_that(fault_steps).is_equal_to(
        [
            ("publish-pypi-on-tag.yml", "fail-build"),
            ("publish-npm.yml", "fail-publish-npm"),
        ],
    )
    # The exact condition, run as the steps run it: each fires on its own
    # name alone, with the annotation the runbook looks for.
    for own, other in (
        ("fail-build", "fail-publish-npm"),
        ("fail-publish-npm", "fail-build"),
    ):
        fired = _run_fault_step(fault=own, release_fault=own)
        assert_that(fired.returncode).described_as(own).is_equal_to(1)
        assert_that(fired.stdout).contains(
            f"::error title=Injected release fault::RELEASE_FAULT={own}",
        )
        for value in (other, ""):
            quiet = _run_fault_step(fault=own, release_fault=value)
            assert_that(quiet.returncode).described_as(f"{own}/{value!r}").is_equal_to(
                0,
            )


def test_validation_switches_are_documented_as_validation_only() -> None:
    """The workflow header, the workflows README and the runbook name both switches."""
    header = (
        _REPO_ROOT / ".github" / "workflows" / "publish-pypi-on-tag.yml"
    ).read_text(encoding="utf-8")
    readme = (_REPO_ROOT / ".github" / "workflows" / "README.md").read_text(
        encoding="utf-8",
    )
    runbook = (_REPO_ROOT / "docs" / "release-validation.md").read_text(
        encoding="utf-8",
    )
    for text, label in ((header, "header"), (readme, "README"), (runbook, "runbook")):
        for needle in (_VALIDATION_CHANNELS_VAR, _RELEASE_FAULT_VAR, "validation"):
            assert_that(text).described_as(f"{label}: {needle}").contains(needle)
    for scenario in ("S1", "S2", "S3", "S4"):
        assert_that(runbook).described_as(scenario).contains(f"## {scenario}")
    assert_that(runbook).contains("npm deprecate")
    assert_that(runbook).contains("exempt")
