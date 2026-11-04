"""Workflow wiring contracts: ci contracts."""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - subprocess runs fixed git argv against this repo
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that
from pathspec import GitIgnoreSpec

from lintro._tool_versions import TOOL_VERSIONS
from lintro.enums.tool_name import ToolName
from tests.integration._tools import ALLOW_VERSION_LAG_ENV
from tests.unit.workflow_wiring_helpers import (
    _API_SMOKE_JOB,
    _API_SMOKE_WORKFLOW,
    _ARTIFACT_ENDPOINTS,
    _ATTESTATION_VERIFY_HOSTS,
    _BLOCK_SCALAR_WITH,
    _BUILD_BINARY_WORKFLOW,
    _BUNDLED_RUST_COMPONENT_PACKAGES,
    _DELIBERATELY_UNWATCHED,
    _GHCR_CLEANUP_LANE_JOBS,
    _GITHUB_PULL_REQUEST_EVENT,
    _HARDEN_RUNNER_GITHUB_META_FLOOR,
    _HARDEN_RUNNER_PIN_RE,
    _LGTM_CI_GITHUB_META_FLOOR,
    _LGTM_CI_NUMBER_INPUTS,
    _LGTM_CI_NUMBER_INPUTS_COMMON,
    _LGTM_CI_QUALITY_LINT_PIN_RE,
    _OIDC_HOST,
    _PIPELINE_RELEVANT_TOP_LEVEL,
    _PIPELINE_SKIPPABLE_TOP_LEVEL,
    _REF_KEYED_PUSH_GROUP_EXEMPTIONS,
    _REGEX_INTENT_TELLS,
    _REPO_ROOT,
    _RESOLVE_PIPELINE_SCRIPT,
    _SIGSTORE_HOSTS,
    _UV_RELEASE_ASSET_HOSTS,
    _VULN_DETECT_ACTION,
    _VULN_GATE_WORKFLOW,
    _VULN_SBOM_ACTION,
    _VULN_SCAN_ACTION,
    _attestation_verifying_jobs,
    _attesting_jobs,
    _auto_rerun_signatures,
    _canonical_lgtm_ci_pin,
    _cosign_oidc_flake_markers,
    _harden_runner_endpoints,
    _hardened_jobs_that_bootstrap_uv,
    _js_named_groups_to_python,
    _load_workflow,
    _normalize_github_expr,
    _render_concurrency_group,
    _resolve_prune_min_age_days,
    _strip_regex_delimiters,
    _tracked_top_level_paths,
    _trigger_can_run_on_main,
    _vuln_gate_job,
    _vuln_gate_step,
    _workflow_paths,
    _workflow_pushes_to_main,
)


def test_version_hook_can_reach_artifact_results_service() -> None:
    """The release metadata download needs artifact API egress (#2965)."""
    release = _load_workflow(name="release-version-pr.yml")
    options = release["jobs"]["version-pr"]["with"]
    allowed = set(str(options["allowed-endpoints"]).split())

    assert_that(options["egress-policy"]).is_equal_to("audit")
    assert_that(allowed).contains(
        "pipelines.actions.githubusercontent.com:443",
        "results-receiver.actions.githubusercontent.com:443",
    )


def test_test_ci_has_no_path_classification_surface() -> None:
    """test-ci must not reintroduce a pipeline classifier (#2108, #2297).

    The Python matrix can never path-skip: ``pipeline-skip`` is hard-false
    because a skipped reusable ``test`` job publishes the uninterpolated
    check name and deadlocks required-check merges. The former ``changes``
    job therefore computed a ``pipeline`` output whose only consumer
    (``stage-coverage-html``) is push-only, while
    ``resolve-pipeline-relevance.sh`` resolves ``pipeline=false`` on
    ``pull_request`` alone — the condition could never be false. Guard the
    whole surface, not just the job name, so it cannot creep back.
    """
    test_ci = _load_workflow(name="test-ci.yml")
    raw = (_REPO_ROOT / ".github" / "workflows" / "test-ci.yml").read_text(
        encoding="utf-8",
    )

    assert_that(test_ci["jobs"]).does_not_contain_key("changes")
    assert_that(raw).does_not_contain("resolve-pipeline-relevance.sh")
    assert_that(raw).does_not_contain("needs.changes.")

    # Exact dependency lists, not a "does not contain 'changes'" subset check:
    # a classifier reintroduced under any other job id would slip past a
    # name-shaped assertion. These are the only edges test-ci may have.
    expected_needs: dict[str, list[str]] = {
        "test-compat": [],
        "test-coverage": [],
        "test-shell": [],
        "test-gate": ["test-compat", "test-coverage", "test-shell"],
        "test-suite-coverage": ["test-gate"],
        "stage-coverage-html": ["test-coverage"],
    }
    assert_that(set(test_ci["jobs"])).is_equal_to(set(expected_needs))
    for job_id, expected in expected_needs.items():
        assert_that(test_ci["jobs"][job_id].get("needs") or []).described_as(
            job_id,
        ).is_equal_to(expected)
    # on.<event>.paths collapses nested required contexts (#1359).
    triggers = test_ci["on"]
    assert_that(triggers).is_not_empty()
    for trigger in triggers.values():
        if isinstance(trigger, dict):
            assert_that(trigger).does_not_contain_key("paths")
            assert_that(trigger).does_not_contain_key("paths-ignore")


def test_test_ci_reusables_never_path_skip() -> None:
    """Reusable callers always run the matrix and never path-skip.

    ``if: '!cancelled()'`` mirrors docker-ci's docker-build gate: the matrix
    runs unless the whole workflow is cancelled, instead of collapsing to
    skipped → false green. With the classifier gone (#2297) the callers
    have no upstream dependency at all, so nothing can skip them.

    ``pipeline-skip`` stays hard-false (#2108): lgtm-ci's skipped ``test``
    job publishes as ``test-compat / inputs.job-name`` rather than the
    org-required ``test-compat / Python Compatibility`` (and the coverage
    equivalent), which deadlocks version-bump merges. Re-enable only when
    that reusable interpolates the skipped job name.
    """
    expected_job_names = {
        "test-compat": "Python Compatibility",
        "test-coverage": "Python Coverage",
    }
    test_ci = _load_workflow(name="test-ci.yml")
    for job_name, published_name in expected_job_names.items():
        job = test_ci["jobs"][job_name]
        assert_that(job.get("needs") or []).is_empty()
        assert_that(job["if"]).is_equal_to("!cancelled()")
        assert_that(job["with"]["pipeline-skip"]).is_false()
        assert_that(job["with"]["job-name"]).is_equal_to(published_name)


def test_version_lag_env_matches_the_plugin_contract() -> None:
    """The gate reads the same env var the plugins do (#1582).

    ``tests/integration/_tools.py`` mirrors lintro's version-lag allowance so
    an allow-listed lagging binary keeps collecting its module. The name is
    spelled once per side; a rename in either would silently re-introduce the
    skip the allowance exists to prevent.
    """
    from lintro.plugins.execution_preparation import _ALLOW_VERSION_LAG_ENV

    assert_that(ALLOW_VERSION_LAG_ENV).is_equal_to(_ALLOW_VERSION_LAG_ENV)


def test_test_ci_matrix_collects_the_integration_suite() -> None:
    """The Python matrix runs tests/integration instead of ignoring it (#465).

    Every integration module gates on ``tests/integration/_tools.py``, which
    skips on a toolless runner and only fails inside the tools image, so the
    hosted matrix can collect the suite without installing any wrapped tool.
    """
    test_ci = _load_workflow(name="test-ci.yml")
    for job_name in ("test-compat", "test-coverage"):
        job = test_ci["jobs"][job_name]
        assert_that(job["with"]["test-path"]).described_as(job_name).is_equal_to(
            "tests",
        )
        # Assert the absence of *any* ignore, not just this path spelling:
        # "--ignore=tests" or "--ignore tests/integration" would exclude the
        # suite again while still passing a substring check for the path.
        assert_that(job["with"]["extra-args"]).described_as(
            job_name,
        ).does_not_contain("--ignore", "tests/integration")


def test_test_ci_runs_the_bats_suites_and_gates_on_them() -> None:
    """CI must run the shell tests and the gate must count their result.

    Fifteen bats files under ``tests/bats`` (build, release-gate, CI and npm
    scripts) ran only through the local pre-push gate: no workflow invoked
    bats, so a broken release script could reach ``main`` with green CI
    (#2659 review). The job uses lgtm-ci's ``reusable-test-shell.yml`` at the
    canonical pin under the runner contract, and ``test-gate`` needs it and
    passes its result to ``evaluate-test-gate.sh``, which requires it.
    """
    test_ci = _load_workflow(name="test-ci.yml")
    job = test_ci["jobs"]["test-shell"]

    assert_that(str(job["uses"])).is_equal_to(
        "lgtm-hq/lgtm-ci/.github/workflows/reusable-test-shell.yml@"
        + _canonical_lgtm_ci_pin(),
    )
    with_block = job["with"]
    assert_that(with_block["test-path"]).is_equal_to("tests/bats")
    assert_that(with_block["tooling-ref"]).is_equal_to(_canonical_lgtm_ci_pin())
    assert_that(with_block["egress-policy"]).is_equal_to("audit")
    assert_that(with_block["allowed-endpoints-mode"]).is_equal_to("replace")
    assert_that(set(str(with_block["allowed-endpoints"]).split())).contains(
        "github.com:443",
        "api.github.com:443",
    )
    # actions: read since lgtm-ci v0.76 (the aggregate job lists artifacts).
    assert_that(job["permissions"]).is_equal_to(
        {"actions": "read", "contents": "read", "pull-requests": "write"},
    )
    assert_that((_REPO_ROOT / "tests" / "bats").is_dir()).is_true()

    gate = test_ci["jobs"]["test-gate"]
    assert_that(gate["needs"]).contains("test-compat", "test-coverage", "test-shell")
    evaluate = [step for step in gate["steps"] if step.get("id") == "gate"]
    assert_that(evaluate).is_length(1)
    assert_that(evaluate[0]["env"]["SHELL_RESULT"]).is_equal_to(
        "${{ needs.test-shell.result }}",
    )
    script = (_REPO_ROOT / "scripts" / "ci" / "evaluate-test-gate.sh").read_text(
        encoding="utf-8",
    )
    assert_that(script).contains(': "${SHELL_RESULT:?}"', "test-shell:${SHELL_RESULT}")


def test_test_ci_suite_coverage_gate_mirrors_test_gate() -> None:
    """Required coverage gate mirrors test-gate with no pipeline=false shortcut.

    The Python matrix always runs (#2108), so a path-skip success shortcut
    would false-green this required check while tests actually failed.
    """
    test_ci = _load_workflow(name="test-ci.yml")
    gate = test_ci["jobs"]["test-suite-coverage"]

    assert_that(gate["needs"]).is_equal_to(["test-gate"])
    assert_that(gate["with"]).does_not_contain_key("pipeline-skip")
    assert_that(gate["with"]["upstream-result"]).is_equal_to(
        "${{ needs.test-gate.outputs.result }}",
    )
    assert_that(gate["with"]["passed-output"]).is_equal_to(
        "${{ needs.test-gate.outputs.passed }}",
    )


def test_every_top_level_path_is_categorized_for_pipeline_relevance() -> None:
    """Every tracked top-level path is explicitly skippable or relevant.

    Deny-by-default means an uncategorized path already triggers the
    pipeline at runtime; this test exists so the categorization is a
    conscious decision rather than an accident of the default.
    """
    top_level = _tracked_top_level_paths()
    categorized = _PIPELINE_SKIPPABLE_TOP_LEVEL | _PIPELINE_RELEVANT_TOP_LEVEL
    uncategorized = sorted(
        path
        for path in top_level
        if path not in categorized and not path.endswith(".md")
    )
    assert_that(uncategorized).described_as(
        "New top-level path(s) are not categorized for docker-ci pipeline "
        "relevance (#1369). Add each one to _PIPELINE_RELEVANT_TOP_LEVEL in "
        "tests/unit/test_workflow_wiring.py (the safe default: anything "
        "that can affect the Docker image, the integration tests, or lint "
        "behavior — it already triggers the pipeline automatically), or — "
        "ONLY for pure prose/static assets — to _PIPELINE_SKIPPABLE_TOP_LEVEL "
        "here AND to is_skippable_path in "
        "scripts/ci/resolve-pipeline-relevance.sh",
    ).is_empty()


def test_pipeline_relevance_categories_are_disjoint() -> None:
    """No top-level path may be both skippable and pipeline-relevant."""
    overlap = _PIPELINE_SKIPPABLE_TOP_LEVEL & _PIPELINE_RELEVANT_TOP_LEVEL
    assert_that(sorted(overlap)).is_empty()


def test_skippable_categorization_matches_resolver_skip_list() -> None:
    """The test's skippable set mirrors the script's actual skip-list.

    Parses the ``is_skippable_path`` case arms out of
    resolve-pipeline-relevance.sh: directory globs returning 0 must equal
    _PIPELINE_SKIPPABLE_TOP_LEVEL, the '*.md' prose rule must be present,
    and the pipeline-relevant carve-outs (test_samples/**,
    docs/.markdownlint-cli2.jsonc) must return 1 and be categorized as
    relevant here.
    """
    script = _RESOLVE_PIPELINE_SCRIPT.read_text(encoding="utf-8")

    skip_dir_globs = set(re.findall(r"^\s*(\S+)/\*\)\s*return 0", script, re.M))
    assert_that(skip_dir_globs).is_equal_to(set(_PIPELINE_SKIPPABLE_TOP_LEVEL))

    skip_file_globs = re.findall(r"^\s*(\*\.\w+)\)\s*return 0", script, re.M)
    assert_that(skip_file_globs).is_equal_to(["*.md"])

    carve_outs = set(re.findall(r"^\s*(\S+)\)\s*return 1", script, re.M))
    assert_that(carve_outs).is_equal_to(
        {"test_samples/*", "docs/.markdownlint-cli2.jsonc"},
    )
    assert_that(_PIPELINE_RELEVANT_TOP_LEVEL).contains("test_samples")


def test_renovate_manages_build_binary_uv_pin() -> None:
    """A Renovate customManager must match the build-binary UV_VERSION line.

    The workflow pin is not a native manager target, so without a regex
    manager whose pattern actually matches the file's text it would silently
    rot while the Dockerfile pin advanced (#1487).
    """
    config = json.loads((_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    workflow_text = (
        _REPO_ROOT / ".github" / "workflows" / _BUILD_BINARY_WORKFLOW
    ).read_text(encoding="utf-8")

    matching = [
        manager
        for manager in config["customManagers"]
        if any(
            _BUILD_BINARY_WORKFLOW in pattern
            for pattern in manager.get("managerFilePatterns", [])
        )
    ]
    assert_that(matching).described_as(
        "no Renovate customManager targets build-binaries.yml",
    ).is_not_empty()

    # The docker-ci semgrep-lock job carries the same pin (#2436); the same
    # manager must cover it or the second site would rot.
    uv_managers = [
        manager
        for manager in matching
        if any("UV_VERSION" in pattern for pattern in manager.get("matchStrings", []))
    ]
    assert_that(uv_managers).is_not_empty()
    assert_that(
        [
            pattern
            for manager in uv_managers
            for pattern in manager["managerFilePatterns"]
        ],
    ).contains(".github/workflows/docker-ci.yml")

    for manager in matching:
        assert_that(manager["packageNameTemplate"]).is_equal_to("astral-sh/uv")
        for match_string in manager["matchStrings"]:
            # Renovate uses JS named groups, `(?<name>...)`; Python wants
            # `(?P<name>...)`.
            pattern = re.sub(r"\(\?<(\w+)>", r"(?P<\1>", match_string)
            found = re.search(pattern, workflow_text)
            assert_that(found).described_as(
                f"matchString {match_string!r} does not match build-binaries.yml",
            ).is_not_none()


def test_renovate_does_not_automerge_golangci_lint_pin() -> None:
    """golangci-lint pin bumps must not automerge independently of the tools digest.

    The regex manager writes only ``ToolName.GOLANGCI_LINT`` in
    ``_tool_versions.py``. The app image copies binaries from the
    digest-pinned ``lintro-tools`` image, so a versions-only merge fails
    Docker verify (#2139, #2220). Automerge stays off so the pin and the
    matching digest can land together.
    """
    config = json.loads((_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    managers = [
        manager
        for manager in config["customManagers"]
        if manager.get("packageNameTemplate") == "golangci/golangci-lint"
    ]
    assert_that(managers).is_not_empty()

    rules = [
        rule
        for rule in config.get("packageRules", [])
        if "golangci/golangci-lint" in (rule.get("matchPackageNames") or [])
        and rule.get("automerge") is False
    ]
    assert_that(rules).described_as(
        "no packageRule disables automerge for golangci/golangci-lint",
    ).is_not_empty()


def test_renovate_manages_html_validate_docs_pin() -> None:
    """A Renovate customManager must match the docs html-validate pin (#2842).

    ``test_html_validate_docs_pin_matches_manifest`` requires
    ``docs/configuration.md`` to stamp ``(currently `<pin>`)``. Without a
    regex manager whose pattern matches that line, every html-validate bump
    PR fails the test until the docs are edited by hand.
    """
    from lintro._tool_versions import get_tool_version

    config = json.loads((_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    config_doc = (_REPO_ROOT / "docs" / "configuration.md").read_text(
        encoding="utf-8",
    )

    matching = [
        manager
        for manager in config["customManagers"]
        if "/^docs/configuration\\.md$/" in manager.get("managerFilePatterns", [])
    ]
    assert_that(matching).described_as(
        "exactly one Renovate customManager must target docs/configuration.md",
    ).is_length(1)

    manager = matching[0]
    assert_that(manager.get("depNameTemplate")).described_as(
        "the docs-pin manager must share the html-validate depName so it lands in "
        "the same branch and the linting-tools group as the package.json bump",
    ).is_equal_to("html-validate")
    assert_that(manager.get("datasourceTemplate")).is_equal_to("npm")

    pin = get_tool_version(tool_name="html-validate")
    assert_that(pin).is_not_none()
    captured = []
    for match_string in manager.get("matchStrings", []):
        # Renovate uses JS named groups, `(?<name>...)`; Python wants
        # `(?P<name>...)`.
        pattern = re.sub(r"\(\?<(\w+)>", r"(?P<\1>", match_string)
        found = re.search(pattern, config_doc)
        if found and "currentValue" in found.groupdict():
            captured.append(found.group("currentValue"))
    assert_that(captured).described_as(
        "no matchString of the docs-pin manager captures the stamped pin in "
        "docs/configuration.md",
    ).contains(pin)


@pytest.mark.parametrize(
    "workflow_name",
    ["docker-ci.yml", "ai-review.yml", _BUILD_BINARY_WORKFLOW],
)
def test_githubapp_free_workflows_pin_a_meta_aware_harden_runner(
    workflow_name: str,
) -> None:
    """Workflows without githubapp.com grants must pin harden-runner >= v2.21.1.

    The guards above forbid the ``*.githubapp.com:443`` grant because v2.21.1
    allows GitHub's hosted-runner watchdog hosts from GitHub meta on its own
    (#2352, #2488). A downgrade of the pin would silently bring the exit-143
    kills back while those guards stay green, so the premise is asserted here.
    The pin comment is read from the raw file: YAML loading drops comments,
    and the repo's pinning policy requires every action pin to carry one.
    ``docker-ci.yml``'s dogfood callers inherit their enforcing harden-runner
    step from lgtm-ci's reusable lint workflow, so that pin has its own floor.

    Args:
        workflow_name: Workflow whose harden-runner pins are under test.
    """
    text = (_REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(
        encoding="utf-8",
    )
    pins = _HARDEN_RUNNER_PIN_RE.findall(text)

    assert_that(pins).described_as(
        f"{workflow_name}: harden-runner pins",
    ).is_not_empty()
    for pin in pins:
        version = tuple(int(part) for part in pin)
        assert_that(version >= _HARDEN_RUNNER_GITHUB_META_FLOOR).described_as(
            f"{workflow_name}: harden-runner v{'.'.join(pin)} is below "
            f"v{'.'.join(map(str, _HARDEN_RUNNER_GITHUB_META_FLOOR))}",
        ).is_true()

    if workflow_name != "docker-ci.yml":
        return
    reusable_pins = _LGTM_CI_QUALITY_LINT_PIN_RE.findall(text)
    assert_that(reusable_pins).described_as("dogfood reusable pins").is_not_empty()
    for pin in reusable_pins:
        version = tuple(int(part) for part in pin)
        assert_that(version >= _LGTM_CI_GITHUB_META_FLOOR).described_as(
            f"docker-ci.yml: reusable-quality-lint v{'.'.join(pin)} is below "
            f"v{'.'.join(map(str, _LGTM_CI_GITHUB_META_FLOOR))}",
        ).is_true()


def test_auto_rerun_covers_tag_publish_workflows() -> None:
    """Auto-rerun must watch publish workflows and not filter to main only.

    Tag publishes set workflow_run.head_branch to the tag name, so
    ``branches: [main]`` would never see Publish - PyPI Production failures.
    Fork abuse is blocked by the same-repo job guard instead (#1487).
    """
    workflow = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    trigger = workflow["on"]["workflow_run"]
    watched = set(trigger["workflows"])

    assert_that(watched).contains("Publish - PyPI Production")
    assert_that(watched).contains("Publish - Homebrew Tap")
    assert_that(watched).contains("Publish - npm")
    # branches: would exclude tag head_branch values; omit it entirely.
    assert_that(trigger).does_not_contain_key("branches")
    assert_that(trigger).does_not_contain_key("branches-ignore")

    # workflow_run matches on the workflow's `name:`, and an unknown name is
    # silently ignored by GitHub — so every watched entry must name a real
    # workflow in this repo.
    declared = {
        _load_workflow(name=path.name)["name"]
        for path in sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    }
    assert_that(watched).described_as(
        "watched workflow_run names must exist as workflow `name:` values",
    ).is_subset_of(declared)

    rerun_if = _normalize_github_expr(workflow["jobs"]["rerun"]["if"])
    assert_that(rerun_if).contains(
        "github.event.workflow_run.head_repository.full_name == github.repository",
    )
    assert_that(rerun_if).contains(
        "github.event.workflow_run.conclusion == 'failure'",
    )


def test_auto_rerun_never_reruns_publish_workflows() -> None:
    """Publish runs are protected from automatic reruns (#2633, lgtm-ci#1003).

    The rc1 publish run (34967569536) was re-run by the auto-rerun net after
    an egress refusal matched a signature, and the rerun reached the npm
    approval gate. The reusable's ``protected-workflows`` input names the
    publish workflow files whose runs are never re-run; both the tag
    pipeline and the standalone npm workflow must be listed. The
    runner-acquisition signature is read from check-run annotations, so the
    caller must grant ``checks: read``.
    """
    workflow = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    job = workflow["jobs"]["rerun"]
    protected = {
        entry.strip()
        for entry in str(job["with"]["protected-workflows"])
        .replace("\n", ",")
        .split(",")
        if entry.strip()
    }
    assert_that(protected).contains("publish-pypi-on-tag.yml", "publish-npm.yml")
    for name in protected:
        assert_that(
            (_REPO_ROOT / ".github" / "workflows" / name).is_file(),
        ).described_as(
            f"protected workflow {name} must exist",
        ).is_true()
    assert_that(job["permissions"]).contains_entry({"checks": "read"})
    assert_that(job["permissions"]).contains_entry({"actions": "write"})


def test_auto_rerun_allows_three_reruns() -> None:
    """Persistent runner-loss failures may receive up to three reruns (#2237)."""
    workflow = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    rerun_inputs = workflow["jobs"]["rerun"]["with"]

    assert_that(rerun_inputs["max-reruns"]).is_equal_to("3")


def test_auto_rerun_covers_every_workflow_that_can_redden_main() -> None:
    """Anything that marks a main commit red must be auto-rerun eligible.

    A pure infra kill (exit 143 / runner shutdown) on a workflow triggered by
    push-to-main leaves main red until a human reruns it by hand. That happened
    to Reporting - Scheduled Lintro Analysis, which was simply absent from the
    watched list, so the rerun mechanism never even evaluated it (#1775).

    Workflows that deliberately stay out are enumerated above with a reason,
    so adding one is a conscious act rather than an oversight.
    """
    workflow = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    watched = set(workflow["on"]["workflow_run"]["workflows"])

    reddens_main: set[str] = set()
    for path in sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        parsed = _load_workflow(name=path.name)
        # Every workflow here quotes `'on':`, so it stays a string key rather
        # than YAML 1.1's boolean True.
        triggers = parsed.get("on") or {}
        # Both shapes attach a status to a main commit: a direct push, and a
        # workflow_run chained off one. Checking only `push` would have missed
        # Reporting - Scheduled Lintro Analysis, which is workflow_run-driven
        # and is precisely the workflow whose absence caused #1775.
        for key in ("push", "workflow_run"):
            trigger = triggers.get(key)
            if isinstance(trigger, dict) and _trigger_can_run_on_main(trigger):
                reddens_main.add(parsed["name"])

    unwatched = reddens_main - watched - _DELIBERATELY_UNWATCHED
    assert_that(unwatched).described_as(
        "workflows that redden main but are not auto-rerun eligible",
    ).is_empty()

    # Keep the exclusion list honest in the other direction too. Without this,
    # a workflow that stops reddening main (renamed, retriggered, deleted)
    # leaves a stale entry behind that still reads as a considered decision,
    # and the next person adding an exclusion inherits dead reasoning.
    assert_that(_DELIBERATELY_UNWATCHED).described_as(
        "every deliberate exclusion must still be a workflow that reddens main",
    ).is_subset_of(reddens_main)


def test_every_attesting_job_allows_the_sigstore_hosts() -> None:
    """Every job that signs an attestation must let Sigstore traffic out.

    Under ``allowed-endpoints-mode: replace`` the caller's list is passed
    verbatim to harden-runner, so a list copied from the pypi preset silently
    drops the Sigstore hosts the reusable's own default carried. The
    v0.160.3a3 checkpoint died that way: the dist attest step got
    ``ECONNREFUSED`` from fulcio.sigstore.dev after a green build (#2562).
    Jobs that mint an OIDC token for the attestation also need the token
    endpoint. A missing allowlist counts as empty, not as exempt.
    """
    jobs = _attesting_jobs()
    names = {(workflow, job) for workflow, job, _, _ in jobs}
    assert_that(names).contains(
        ("publish-pypi-on-tag.yml", "pypi-build"),
        ("publish-pypi-on-tag.yml", "docker-promote"),
    )
    assert_that([job for job in names if job[0] == "build-binaries.yml"]).is_not_empty()

    offenders: list[str] = []
    for workflow, job, endpoints, needs_oidc in jobs:
        if endpoints is None:
            continue
        required = set(_SIGSTORE_HOSTS)
        if needs_oidc:
            required.add(_OIDC_HOST)
        missing = sorted(required - endpoints)
        if missing:
            offenders.append(f"{workflow}:{job} missing {missing}")
    assert_that(offenders).described_as(
        "attesting jobs whose replace-mode allowlist blocks Sigstore",
    ).is_empty()


def test_every_attestation_verifying_job_allows_the_attestation_store() -> None:
    """Every job running ``gh attestation verify`` must reach the bundle store.

    ``gh attestation verify`` reads the attestations API, then downloads the
    Sigstore bundle from GitHub's attestation store on Azure blob storage and
    checks it against the TUF root. The S1 checkpoint v0.160.3rc1 died in
    docker-promote's "Verify attestations on the staging digests" step
    because that job's allowlist had no ``*.blob.core.windows.net:443``
    while release-gate's did (#2633). A missing allowlist counts as empty,
    and an expression-valued allowlist fails closed: none of these jobs uses
    one today, and a future resolver job must extend this test to check the
    resolved list rather than be skipped.
    """
    jobs = _attestation_verifying_jobs()
    names = {(workflow, job) for workflow, job, _ in jobs}
    assert_that(names).contains(
        ("publish-pypi-on-tag.yml", "release-gate"),
        ("publish-pypi-on-tag.yml", "docker-promote"),
        ("publish-npm.yml", "stage"),
    )

    offenders: list[str] = []
    for workflow, job, endpoints in jobs:
        if endpoints is None:
            offenders.append(f"{workflow}:{job} uses an expression allowlist")
            continue
        missing = sorted(_ATTESTATION_VERIFY_HOSTS - endpoints)
        if missing:
            offenders.append(f"{workflow}:{job} missing {missing}")
    assert_that(offenders).described_as(
        "attestation-verifying jobs whose allowlist blocks the verifier",
    ).is_empty()


def test_all_lgtm_ci_refs_use_the_canonical_pin() -> None:
    """Keep canonical pins except the independently adopted repair callers.

    Guards the repo-wide invariant from #1280: `uses:` refs,
    `tooling-ref:` inputs, and manual `actions/checkout` steps targeting
    lgtm-hq/lgtm-ci all point at the same lgtm-ci commit, so pins cannot
    silently drift apart again. Only the named repair callers have independent
    exact pins; tags, branches and short SHAs remain offenders.
    """
    canonical = _canonical_lgtm_ci_pin()
    # Gate-only adoption needs the released #1197 retry recovery and #1207
    # block-egress repair without migrating unrelated reusable callers.
    independent_pins = {
        "all-checks-gate.yml": "8f0fd9339df5d30a529aa572ceb51345b8be33a0",
        # #3093: v0.83.11's verified Trivy acquisition, tools-image caller only.
        "docker-tools-publish.yml": "cade5e687630ce3208bf914d0b1590145f3d569e",
        # #3089: the ordinary-main publisher must select the same repair.
        "docker-tools-promote.yml": "cade5e687630ce3208bf914d0b1590145f3d569e",
    }
    # #3200: only the release Docker workflow and its tooling adopt Trivy's
    # verified acquisition. Other actions in this file keep the canonical pin.
    release_repair_refs = {
        (
            ".github/workflows/docker-build-publish.yml",
            action,
            "cade5e687630ce3208bf914d0b1590145f3d569e",
        )
        for action in (
            "lgtm-hq/lgtm-ci/.github/workflows/reusable-docker.yml",
            "tooling-ref",
        )
    }
    ref_pattern = re.compile(
        r"(lgtm-hq/lgtm-ci/[^@\s]+)@([^\s#]+)|tooling-ref:\s*[\"']?([^\"'\s#]+)",
    )
    offenders: list[str] = []
    for path in _workflow_paths():
        expected_pin = independent_pins.get(path.name, canonical)
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            for match in ref_pattern.finditer(line):
                ref = match.group(2) or match.group(3)
                identity = (
                    path.relative_to(_REPO_ROOT).as_posix(),
                    match.group(1) or "tooling-ref",
                    ref,
                )
                if identity in release_repair_refs:
                    continue
                if ref != expected_pin:
                    offenders.append(f"{path.name}:{lineno}: {ref}")

        # Manual lgtm-ci tooling checkouts pin via a separate `ref:` field
        # (e.g. site-quality.yml); a bare `ref:` regex would false-positive
        # on checkouts of other repositories, so walk the parsed YAML.
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                with_block = step.get("with") or {}
                if with_block.get("repository") != "lgtm-hq/lgtm-ci":
                    continue
                if with_block.get("ref") != expected_pin:
                    offenders.append(
                        f"{path.name}:{job_id}: checkout ref {with_block.get('ref')!r}",
                    )

    assert_that(offenders).is_empty()


def test_every_odd_shaped_lgtm_ci_pin_is_renovate_managed() -> None:
    """Pin sites Renovate cannot see must be taught to it, not left to drift.

    Renovate's github-actions manager rewrites ``uses:`` refs, and the org
    preset covers ``tooling-ref:`` inputs. Anything else holding the same SHA
    is invisible to it, so a bump updates the rest of the repo and strands that
    line — breaking the single-pin invariant (#1280) on every release. The
    v0.59.2 pin sat 22 releases stale exactly this way, which also left the
    ``overwrite: true`` retry fix (#1737) unadopted long after it shipped.

    Rather than name the known offender, derive the set: any lgtm-ci SHA in a
    workflow that is not a ``uses:`` or ``tooling-ref:`` line must be matched
    by one of this repo's own ``customManagers`` regexes (#1771).
    """
    canonical = _canonical_lgtm_ci_pin()
    renovate = json.loads(
        (_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"),
    )
    # Keep each manager's regexes bound to the files it is scoped to. Renovate
    # only applies a customManager where its managerFilePatterns match, so
    # flattening every matchStrings across all managers would let a regex
    # scoped to one file vouch for a pin in another -- reporting an unmanaged
    # site as covered, which is the exact blindness #1771 exists to remove.
    scoped_managers = [
        (
            [
                re.compile(_strip_regex_delimiters(file_pattern))
                for file_pattern in manager.get("managerFilePatterns") or []
            ],
            [
                re.compile(_js_named_groups_to_python(pattern))
                for pattern in manager.get("matchStrings") or []
            ],
        )
        for manager in renovate.get("customManagers") or []
    ]

    unmanaged: list[str] = []
    for path in _workflow_paths():
        relative = path.relative_to(_REPO_ROOT).as_posix()
        applicable = [
            pattern
            for file_matchers, patterns in scoped_managers
            if any(matcher.search(relative) for matcher in file_matchers)
            for pattern in patterns
        ]
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if canonical not in line:
                continue
            if "uses:" in line or "tooling-ref:" in line:
                continue
            if not any(pattern.search(line) for pattern in applicable):
                unmanaged.append(f"{relative}:{lineno}: {line.strip()}")

    assert_that(unmanaged).described_as(
        "lgtm-ci pin sites no Renovate manager updates",
    ).is_empty()


def test_stage_coverage_html_allows_setup_uv_manifest_host() -> None:
    """Coverage staging must allow raw.githubusercontent.com for setup-uv.

    Without this host, astral-sh/setup-uv cannot fetch its versions manifest
    under harden-runner block mode, the staging job fails, and CI - Tests goes
    red on main even when the test gate passed (#1227).
    """
    workflow = _load_workflow(name="test-ci.yml")
    steps = workflow["jobs"]["stage-coverage-html"]["steps"]
    harden = next(step for step in steps if step.get("name") == "Harden Runner")
    allowed = set(harden["with"]["allowed-endpoints"].split())

    assert_that(allowed).contains("raw.githubusercontent.com:443")
    assert_that(allowed).contains("astral.sh:443")
    assert_that(allowed).contains("releases.astral.sh:443")
    assert_that(allowed).contains("release-assets.githubusercontent.com:443")
    assert_that(allowed).contains("github-releases.githubusercontent.com:443")


@pytest.mark.parametrize("job_id", ["test-compat", "test-coverage"])
def test_test_jobs_omit_pypi_publish_endpoints(job_id: str) -> None:
    """Test jobs must not allowlist PyPI publish/alt-index hosts (#1351).

    ``pytest`` only downloads from real PyPI. ``allowed-endpoints-mode:
    replace`` is load-bearing so ``egress-preset: pypi`` cannot merge
    publish hosts back in. ``upload.test.pypi.org`` was never in this
    caller list; the omit assertion keeps the preset from reintroducing it.

    Args:
        job_id: Test job whose egress allowlist is under test.
    """
    workflow = _load_workflow(name="test-ci.yml")
    job_with = workflow["jobs"][job_id]["with"]
    allowed = set(job_with["allowed-endpoints"].split())

    assert_that(job_with["allowed-endpoints-mode"]).is_equal_to("replace")
    assert_that(allowed).contains("pypi.org:443")
    assert_that(allowed).contains("files.pythonhosted.org:443")
    assert_that(allowed).does_not_contain("test.pypi.org:443")
    assert_that(allowed).does_not_contain("upload.pypi.org:443")
    assert_that(allowed).does_not_contain("upload.test.pypi.org:443")


@pytest.mark.parametrize("job_id", ["test-compat", "test-coverage"])
def test_test_jobs_allow_setup_uv_github_release_fallback(job_id: str) -> None:
    """Test jobs must allow setup-uv fallback while the Astral mirror lags.

    The explicit allowlist uses replace semantics. When a newly published uv
    version is not yet mirrored at releases.astral.sh, setup-uv falls back to
    GitHub Releases and follows redirects to the release-asset hosts.

    Args:
        job_id: Test job whose egress allowlist is under test.
    """
    workflow = _load_workflow(name="test-ci.yml")
    allowed = set(workflow["jobs"][job_id]["with"]["allowed-endpoints"].split())

    assert_that(allowed).contains(
        "release-assets.githubusercontent.com:443",
        "github-releases.githubusercontent.com:443",
    )


def test_deploy_pages_pins_bundler_with_github_token() -> None:
    """Pages deploy must use lgtm-ci tooling that exports GH_TOKEN to gh.

    reusable-deploy-site-with-reports checks out tooling-ref for
    bundle-workflow-artifacts. v0.32.3 omitted GH_TOKEN; v0.32.4+ (lgtm-ci#300)
    sets ``GH_TOKEN: ${{ github.token }}``. Stay on the repo-standard v0.52.4 pin.
    """
    canonical = _canonical_lgtm_ci_pin()
    workflow = _load_workflow(name="deploy-pages.yml")
    deploy = workflow["jobs"]["deploy"]
    uses = deploy["uses"]
    tooling_ref = deploy["with"]["tooling-ref"]

    assert_that(uses).contains(canonical)
    assert_that(uses).contains("reusable-deploy-site-with-reports.yml")
    assert_that(tooling_ref).contains(canonical)
    # v0.52.4 build job requests actions: write (lgtm-ci#415 rerun
    # self-heal); a lower caller grant is a parse-time startup_failure.
    assert_that(deploy["permissions"]).contains_entry({"actions": "write"})
    assert_that(deploy["permissions"]).contains_entry({"pages": "write"})
    assert_that(deploy["permissions"]).contains_entry({"id-token": "write"})


def test_ghcr_cleanup_sweeps_ephemeral_ci_tags() -> None:
    """Scheduled maintenance owns the age-based CI-tag sweep (#1138)."""
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    sweep = cleanup["jobs"]["sweep-ci-tags"]

    assert_that(sweep["permissions"]).is_equal_to(
        {
            "contents": "read",
            "packages": "write",
        },
    )
    triggers = cleanup["on"]
    assert_that(triggers).contains_key("schedule")
    run_steps = [
        step.get("run", "")
        for step in sweep["steps"]
        if isinstance(step, dict) and step.get("run")
    ]
    assert_that(run_steps).contains(
        "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh",
    )
    # Dispatch inputs: prune keeps min_age_days=7; sweep has its own 91d input
    # so a no-override manual dispatch does not silently lengthen untagged
    # prune retention (CodeRabbit on #1645).
    assert_that(triggers).contains_key("workflow_dispatch")
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert_that(inputs).contains_key("min_age_days")
    assert_that(inputs["min_age_days"]["default"]).is_equal_to(7)
    assert_that(inputs).contains_key("sweep_min_age_days")
    assert_that(inputs["sweep_min_age_days"]["default"]).is_equal_to(91)
    assert_that(inputs).contains_key("dry_run")
    prune = cleanup["jobs"]["prune-untagged"]
    assert_that(str(prune["with"].get("min-age-days", ""))).contains(
        "inputs.min_age_days",
    )
    assert_that(str(prune["with"].get("min-age-days", ""))).does_not_contain(
        "sweep_min_age_days",
    )
    sweep_env = next(
        (step.get("env") or {})
        for step in sweep["steps"]
        if isinstance(step, dict)
        and step.get("run") == "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh"
    )
    assert_that(str(sweep_env.get("MIN_AGE_DAYS", ""))).contains("91")
    assert_that(str(sweep_env.get("MIN_AGE_DAYS", ""))).contains(
        "inputs.sweep_min_age_days",
    )


@pytest.mark.parametrize(
    ("dispatch_input", "expected"),
    [
        ("", 7),  # scheduled path: the dispatch input is empty -> '7' default
        ("7", 7),  # dispatched default arrives as the string '7'
        ("14", 14),  # dispatched override coerces to a number
    ],
)
def test_ghcr_cleanup_prune_min_age_resolves_to_a_number(
    dispatch_input: str,
    expected: int,
) -> None:
    """The prune min-age resolves to a number on every trigger path (#2603).

    A number-typed ``workflow_dispatch`` input arrives as a string and the
    scheduled path leaves the input empty; either passed straight through,
    the reusable call is rejected at plan time ("Unexpected value '7'",
    runs 34766253077 and 34766393903). The ``|| '7'`` default makes the
    empty scheduled case resolve to 7 days.
    """
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    for job_name in ("prune-untagged", "prune-untagged-base"):
        expression = str(cleanup["jobs"][job_name]["with"]["min-age-days"])
        assert_that(
            _resolve_prune_min_age_days(
                expression,
                dispatch_input=dispatch_input,
            ),
        ).is_equal_to(expected)


def test_ghcr_cleanup_prune_legs_forward_a_number_timeout() -> None:
    """The caller raises the reusable's prune job timeout to 30 (#2603).

    The reusable defaults ``timeout-minutes`` to 10, and its default cancelled
    the ``py-lintro`` untagged prune leg mid-enumeration (run 34774902409) —
    the leg never reported, so the sweep silently lost a package. Both prune
    callers must forward a bare number literal (the plan-time string gotcha
    from the min-age fix applies here too), not a block scalar or string.
    """
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    for job_name in ("prune-untagged", "prune-untagged-base"):
        timeout = cleanup["jobs"][job_name]["with"]["timeout-minutes"]
        assert_that(timeout).described_as(job_name).is_instance_of(int)
        assert_that(timeout).described_as(job_name).is_equal_to(30)


@pytest.mark.parametrize(
    ("workflow", "job", "matrix"),
    [
        (
            "test-built-package.yml",
            "test-package-install",
            {"package-type": "wheel"},
        ),
        ("site-quality.yml", None, None),
    ],
)
def test_main_push_concurrency_groups_key_on_commit_sha(
    workflow: str,
    job: str | None,
    matrix: dict[str, str] | None,
) -> None:
    """Push events must get a per-commit concurrency slot (#1673).

    ``github.ref`` is ``refs/heads/main`` for every push to main, so a group
    keyed on it collapses consecutive merges into one slot and
    ``cancel-in-progress`` kills the earlier commit's run. Pull requests must
    keep the ref-keyed behaviour so a force-push still supersedes the stale run.

    Args:
        workflow: Workflow file name under ``.github/workflows``.
        job: Job id owning the concurrency block, or ``None`` for the
            workflow-level block.
        matrix: ``matrix`` context values the group interpolates, if any.
    """
    data = _load_workflow(name=workflow)
    scope = data if job is None else data["jobs"][job]
    concurrency = scope["concurrency"]
    assert_that(concurrency["cancel-in-progress"]).is_true()

    group = concurrency["group"]
    main_ref = "refs/heads/main"
    first = _render_concurrency_group(
        group,
        event_name="push",
        ref=main_ref,
        sha="a" * 40,
        matrix=matrix,
    )
    second = _render_concurrency_group(
        group,
        event_name="push",
        ref=main_ref,
        sha="b" * 40,
        matrix=matrix,
    )
    assert_that(first).is_not_equal_to(second)
    assert_that(first).contains("a" * 40)
    assert_that(first).does_not_contain(main_ref)

    # A pull request keeps its ref-keyed slot, so a force-push (new sha, same
    # ref) still lands in the same group and cancels the outdated run.
    pr_ref = "refs/pull/1673/merge"
    before = _render_concurrency_group(
        group,
        event_name=_GITHUB_PULL_REQUEST_EVENT,
        ref=pr_ref,
        sha="c" * 40,
        matrix=matrix,
    )
    after = _render_concurrency_group(
        group,
        event_name=_GITHUB_PULL_REQUEST_EVENT,
        ref=pr_ref,
        sha="d" * 40,
        matrix=matrix,
    )
    assert_that(before).is_equal_to(after)
    assert_that(before).contains(pr_ref)


def test_no_main_push_workflow_cancels_itself_on_ref() -> None:
    """Audit every push-to-main workflow for the #1673 self-cancel pattern.

    A group keyed only on ``github.ref`` combined with a literal
    ``cancel-in-progress: true`` means consecutive main merges cancel each
    other. Guarding cancellation with ``github.ref != 'refs/heads/main'`` or
    keying the group on ``github.sha`` both avoid it.
    """
    offenders: list[str] = []
    workflows = sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert_that(workflows).is_not_empty()

    for path in workflows:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not _workflow_pushes_to_main(data):
            continue
        scopes: list[tuple[str, Any]] = [(path.name, data)]
        scopes.extend(
            (f"{path.name}:{job_id}", job)
            for job_id, job in (data.get("jobs") or {}).items()
            if isinstance(job, dict)
        )
        for label, scope in scopes:
            concurrency = scope.get("concurrency")
            if not isinstance(concurrency, dict):
                continue
            if concurrency.get("cancel-in-progress") is not True:
                continue
            group = str(concurrency.get("group", ""))
            if "github.sha" in group:
                continue
            if label in _REF_KEYED_PUSH_GROUP_EXEMPTIONS:
                continue
            offenders.append(label)

    assert_that(offenders).is_empty()


def test_dependency_vuln_gate_job_exists() -> None:
    """The pre-merge gate job exists and scans the repo dependency set."""
    job = _vuln_gate_job()
    assert_that(job["name"]).contains("Dependency Vulnerability Gate")

    sbom_step = _vuln_gate_step(uses_prefix=_VULN_SBOM_ACTION)
    assert_that(sbom_step["with"]).contains_entry({"target": "."})
    assert_that(sbom_step["with"]).contains_entry({"target-type": "dir"})

    scan_step = _vuln_gate_step(uses_prefix=_VULN_SCAN_ACTION)
    assert_that(scan_step["with"]).contains_entry({"target-type": "sbom"})
    assert_that(str(scan_step["with"]["target"])).contains("steps.sbom.outputs")


def test_dependency_vuln_gate_matches_release_fail_on_threshold() -> None:
    """Pre-merge threshold must equal the release gate's (#1667).

    A pre-merge scan looser than publish-pypi-on-tag.yml's ``sbom`` job
    manufactures false confidence, so the threshold is asserted equal to the
    release gate rather than merely asserted to be ``high``.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    release_threshold = publish["jobs"]["sbom"]["with"]["fail-on-severity"]

    scan_step = _vuln_gate_step(uses_prefix=_VULN_SCAN_ACTION)

    assert_that(scan_step["with"]["fail-on"]).is_equal_to(release_threshold)


def test_dependency_vuln_gate_shares_release_tooling_ref() -> None:
    """Gate actions are pinned at the release gate's lgtm-ci tooling-ref."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    release_ref = str(publish["jobs"]["sbom"]["with"]["tooling-ref"])

    pinned = [
        step["uses"]
        for step in _vuln_gate_job()["steps"]
        if step.get("uses", "").startswith("lgtm-hq/lgtm-ci/")
    ]

    assert_that(pinned).is_not_empty()
    for uses in pinned:
        # Exact equality on the ref after ``@`` — a substring/``contains``
        # check would also pass on a longer ref that merely ends with the
        # release pin, which is the drift this guard exists to forbid.
        ref = uses.split("@", 1)[1]
        assert_that(ref).is_equal_to(release_ref)


def test_dependency_vuln_gate_is_required_check_safe() -> None:
    """The gate must always report its context (#1196).

    A ``paths:`` filter, or a job-level ``if:``, would stop the context from
    ever being created — which deadlocks the merge queue the moment the
    context is added to the ``checks-py-lintro`` ruleset. Path scoping
    therefore lives on the steps inside the job, not on the trigger or the job.
    """
    workflow = _load_workflow(name=_VULN_GATE_WORKFLOW)
    triggers = workflow["on"]

    assert_that(triggers).contains_key(_GITHUB_PULL_REQUEST_EVENT)
    assert_that(triggers).contains_key("merge_group")
    for event in (_GITHUB_PULL_REQUEST_EVENT, "merge_group"):
        assert_that(triggers[event] or {}).does_not_contain_key("paths")
        assert_that(triggers[event] or {}).does_not_contain_key("paths-ignore")

    job = _vuln_gate_job()
    assert_that(job).does_not_contain_key("if")
    # A skipped reusable *caller* collapses its nested contexts, so the gate
    # must stay a plain job that always reports its own check run.
    assert_that(job).does_not_contain_key("uses")
    assert_that(job).contains_key("runs-on")

    # The expensive steps are the ones that skip.
    for prefix in (_VULN_SBOM_ACTION, _VULN_SCAN_ACTION):
        step_if = _normalize_github_expr(_vuln_gate_step(uses_prefix=prefix)["if"])
        assert_that(step_if).contains("steps.changes.outputs.changes")


def test_dependency_vuln_gate_scopes_to_dependency_paths() -> None:
    """The scan covers every language manifest the release gate scans.

    The release gate is ``syft scan dir:.`` over the whole repo, so the
    pre-merge filter must react to any file that can change that graph — not
    just the Python lock — or it is looser than the release gate (#1667). This
    repo's SBOM is cataloged from JavaScript, Python, Rust and Go manifests,
    so all four families must be watched, at any depth.
    """
    detect_step = _vuln_gate_step(uses_prefix=_VULN_DETECT_ACTION)
    filters = yaml.safe_load(detect_step["with"]["filters"])
    deps = filters["deps"]

    assert_that(filters).contains_key("deps")
    # Python — `*requirements*.txt` basenames ("requirements" anywhere, e.g.
    # dev-requirements.txt) and the requirements/ dir layout at any depth.
    assert_that(deps).contains("**/uv.lock")
    assert_that(deps).contains("**/pyproject.toml")
    assert_that(deps).contains("**/*requirements*.txt")
    assert_that(deps).contains("**/requirements/*.txt")
    assert_that(deps).contains("**/requirements/**/*.txt")
    # JavaScript / TypeScript (root bun.lock, apps/site, npm/ manifests)
    assert_that(deps).contains("**/package.json")
    assert_that(deps).contains("**/bun.lock")
    # Rust and Go (test_samples manifests are in the scanned tree)
    assert_that(deps).contains("**/Cargo.lock")
    assert_that(deps).contains("**/Cargo.toml")
    assert_that(deps).contains("**/go.mod")
    assert_that(deps).contains("**/go.sum")
    # The gate exercises itself and the real release gate.
    assert_that(deps).contains(".github/workflows/publish-pypi-on-tag.yml")
    # grype's ignore rules change the verdict without touching a manifest.
    assert_that(deps).contains(".grype.yaml")


def test_dependency_vuln_gate_filter_is_pure_allow_list() -> None:
    """The ``deps`` filter carries no ``!`` negations (dorny ``some`` trap).

    dorny/paths-filter defaults to ``predicate-quantifier: some`` and the
    lgtm-ci wrapper does not override it, so a standalone negation like
    ``!**/node_modules/**`` would match every file *outside* node_modules and
    force ``deps`` true on nearly every PR — it does not subtract. Excluding
    vendored trees would also make this gate looser than the release gate,
    which scans them via ``syft scan dir:.``. So the filter must stay a pure
    allow-list.
    """
    detect_step = _vuln_gate_step(uses_prefix=_VULN_DETECT_ACTION)
    patterns = yaml.safe_load(detect_step["with"]["filters"])["deps"]

    negations = [p for p in patterns if p.startswith("!")]
    assert_that(negations).is_empty()


def test_dependency_vuln_gate_filter_globs_match_committed_manifests() -> None:
    """Every committed dependency manifest matches the gate's ``deps`` filter.

    Guards the #1667 drift concern directly: if a real manifest the release
    gate scans is not matched by any ``deps`` pattern, a PR could change the
    scanned graph through it without the pre-merge gate reacting. Enumerates
    the repo's tracked manifests and asserts each matches a pattern, using the
    same picomatch-style semantics dorny applies (pure allow-list, ``some``
    quantifier — a file matches the filter if it matches any pattern).
    """
    detect_step = _vuln_gate_step(uses_prefix=_VULN_DETECT_ACTION)
    patterns = yaml.safe_load(detect_step["with"]["filters"])["deps"]

    tracked = subprocess.run(  # nosec B603 B607 - fixed argv against this repo
        ["git", "ls-files"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    # Genuine dependency-manifest basenames the release gate's syft scan
    # catalogs. Deliberately excludes ``setup.py`` / ``setup.cfg``: the filter
    # still lists them for future-proofing, but this repo's only committed
    # ``setup.py`` is a Click command module (lintro/cli_utils/commands/), not
    # packaging metadata, and syft does not catalog it — so it is not a manifest
    # this drift guard should assert on.
    manifest_names = {
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
    }

    # Match with pathspec's gitignore semantics rather than a hand-rolled
    # matcher. gitignore globs support the ``**`` globstar natively, closely
    # mirroring the picomatch semantics dorny/paths-filter applies: a leading
    # ``**/`` matches at any depth including the repo root (so ``**/uv.lock``
    # matches both ``uv.lock`` and ``a/b/uv.lock``), and ``*`` within a segment
    # does not cross ``/``. The filter is a pure allow-list, so a manifest is
    # covered iff it matches at least one pattern (``GitIgnoreSpec.match_file``).
    spec = GitIgnoreSpec.from_lines(patterns)

    manifests = [p for p in tracked if Path(p).name in manifest_names]
    # requirements files are a glob family: `*requirements*.txt` basenames, and
    # any `.txt` under a `requirements/` directory at any depth
    # (requirements/base.txt, requirements/dev/base.txt).
    manifests += [
        p
        for p in tracked
        if p.endswith(".txt")
        and ("requirements" in Path(p).name or "requirements" in Path(p).parent.parts)
    ]
    assert_that(manifests).is_not_empty()

    unmatched = [path for path in manifests if not spec.match_file(path)]

    assert_that(unmatched).is_empty()


def test_dependency_vuln_gate_runs_on_grype_config_only_change() -> None:
    """A PR that only edits ``.grype.yaml`` still runs the grype scan.

    The scan action invokes grype in the checkout, where it loads
    ``.grype.yaml``; removing an ignore there changes the gate's verdict, so a
    config-only diff must match the ``deps`` filter or the change would go
    unverified until the release scan (#1667).
    """
    detect_step = _vuln_gate_step(uses_prefix=_VULN_DETECT_ACTION)
    patterns = yaml.safe_load(detect_step["with"]["filters"])["deps"]
    spec = GitIgnoreSpec.from_lines(patterns)

    assert_that(spec.match_file(".grype.yaml")).is_true()


def test_renovate_does_not_track_rustfmt_or_clippy_independently() -> None:
    """rustfmt/clippy pins are toolchain readouts, not Renovate knobs (#2205).

    Independent managers open unmergeable PRs (#1605) because those tags are
    source milestones, not installable artifacts. rustc remains the single
    rust-family manager; component records bump in the same toolchain PR.
    """
    config = json.loads(
        (_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"),
    )
    managers = config.get("customManagers") or []
    tracked = {
        manager.get("packageNameTemplate")
        for manager in managers
        if manager.get("packageNameTemplate")
    }
    for package in _BUNDLED_RUST_COMPONENT_PACKAGES:
        assert_that(tracked).does_not_contain(package)

    rustc = [
        manager
        for manager in managers
        if manager.get("packageNameTemplate") == "rust-lang/rust"
    ]
    assert_that(rustc).is_length(1)
    assert_that(rustc[0]["description"]).contains("#2205")

    grouped_components = [
        package
        for package in _BUNDLED_RUST_COMPONENT_PACKAGES
        if any(
            package in (rule.get("matchPackageNames") or [])
            for rule in config.get("packageRules") or []
        )
    ]
    assert_that(grouped_components).is_empty()

    match_strings = " ".join(
        " ".join(manager.get("matchStrings") or []) for manager in managers
    )
    assert_that(match_strings).does_not_contain("ToolName.CLIPPY")
    assert_that(match_strings).does_not_contain("ToolName.RUSTFMT")

    assert_that(TOOL_VERSIONS[ToolName.CLIPPY]).is_equal_to(
        TOOL_VERSIONS[ToolName.RUSTC],
    )

    versions = (_REPO_ROOT / "lintro" / "_tool_versions.py").read_text(
        encoding="utf-8",
    )
    assert_that(versions).contains("bump only alongside rustc (#2205)")


def test_renovate_does_not_track_cppcheck() -> None:
    """The cppcheck pin follows Debian's package, not upstream's tags.

    Cppcheck ships no portable single binary, so both the tools image and the
    app-image ``install-tools.sh`` bridge install Debian's package. The
    manifest-vs-image gate requires the installed version to *equal* the
    manifest version, so a Renovate-driven bump to an upstream tag apt cannot
    supply would fail CI permanently rather than merely lag. The pin moves
    only when the ``python:3.14-slim`` base image changes Debian release.
    """
    config = json.loads(
        (_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"),
    )
    managers = config.get("customManagers") or []

    tracked = {
        manager.get("packageNameTemplate")
        for manager in managers
        if manager.get("packageNameTemplate")
    }
    assert_that(tracked).does_not_contain("danmar/cppcheck")

    match_strings = " ".join(
        " ".join(manager.get("matchStrings") or []) for manager in managers
    )
    assert_that(match_strings).does_not_contain("ToolName.CPPCHECK")

    grouped = [
        package
        for package in ("cppcheck", "danmar/cppcheck")
        if any(
            package in (rule.get("matchPackageNames") or [])
            for rule in config.get("packageRules") or []
        )
    ]
    assert_that(grouped).is_empty()

    versions = (_REPO_ROOT / "lintro" / "_tool_versions.py").read_text(
        encoding="utf-8",
    )
    assert_that(versions).contains("NOT Renovate-managed")


def test_auto_rerun_passes_cosign_oidc_flake_signatures() -> None:
    """The auto-rerun matcher must know the cosign ambient-OIDC signatures.

    #1646 retries the transient OIDC token-fetch flake in-step; when that
    bounded retry is exhausted the signing job still fails and the publish
    run needs a run-level re-run. The auto-rerun safety net only fires when
    the failed-job logs match a known signature, so the reusable matcher
    receives the cosign flake markers via its ``signatures`` input (#1689).
    Upstream matches with ``grep -qF``, so these must stay fixed strings.
    """
    signatures = _auto_rerun_signatures()

    assert_that(signatures).contains("fetching ambient OIDC credentials")
    assert_that(signatures).contains("retrieving ID token")
    assert_that(signatures).contains("reading ID token")


def test_auto_rerun_signatures_cover_in_step_retry_markers() -> None:
    """Every in-step cosign retry marker must also reach the auto-rerun net.

    The safety net inspects the final failed attempt's logs, which contain
    whichever marker the retry loop in scripts/ci/cosign-sign-images.sh
    matched. A marker missing from the workflow ``signatures`` input would
    leave that exhausted-retry failure mode needing a manual backfill.
    """
    signatures = _auto_rerun_signatures()
    markers = _cosign_oidc_flake_markers()

    assert_that(markers).is_not_empty()
    assert_that(markers).is_subset_of(signatures)


def test_auto_rerun_signatures_are_fixed_strings() -> None:
    """Extra signatures must be plain fixed strings, not regexes.

    ``rerun-on-infra-failure.sh`` matches with ``grep -qF``, so a regex or
    an anchor would be compared literally and silently never match. The check
    targets constructs that betray regex *intent* rather than any
    metacharacter, since literal punctuation is legitimate under ``-F``.
    """
    signatures = _auto_rerun_signatures()

    assert_that(signatures).is_not_empty()
    for signature in signatures:
        for tell in _REGEX_INTENT_TELLS:
            assert_that(re.search(tell, signature)).described_as(
                f"{signature!r} looks like a regex ({tell})",
            ).is_none()


def test_auto_rerun_matches_docker_hub_buildx_pull_timeout() -> None:
    """The matcher must know the Docker Hub buildkit-pull timeout.

    `Setup Docker Buildx` boots buildkit by pulling moby/buildkit from
    Docker Hub. When Docker Hub is slow the daemon times out and the job
    dies before doing any real work -- purely transient, and not a
    harden-runner block (registry-1.docker.io:443 is already allowed).
    None of lgtm-ci's default signatures match it, so the v0.91.42 release
    run (30148763859, Merge Manifests job 89692290242) was never
    auto-rerun. The signature is scoped to the registry URL rather than a
    bare "context deadline exceeded", which would absorb genuine timeouts
    elsewhere that deserve a human.
    """
    signatures = _auto_rerun_signatures()

    assert_that(signatures).contains(
        'Get "https://registry-1.docker.io/v2/": context deadline exceeded',
    )
    # A bare timeout string is too broad to auto-rerun on.
    assert_that(signatures).does_not_contain("context deadline exceeded")


def test_ghcr_cleanup_sweeps_commit_tags_with_the_shared_safety_rule() -> None:
    """Per-commit tags must be swept, reusing the ci-* sweeper's guardrails.

    Unbounded ``sha-<commit>`` growth is not only storage: the tag list is what
    Renovate's docker datasource enumerates to find newer versions, and past
    1000 tags the first page held no recent release at all (#1590).

    The sweep must go through ``sweep-ci-ghcr-tags.sh`` rather than a bespoke
    deletion path, because that script only deletes versions whose *every* tag
    matches the prefix — so a release carrying both ``sha-<commit>`` and a
    version tag can never be removed by it.
    """
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    jobs = cleanup["jobs"]
    assert_that(jobs).contains_key("sweep-sha-tags")

    job = jobs["sweep-sha-tags"]
    sweep_steps = [
        step
        for step in job["steps"]
        if step.get("run") == "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh"
    ]
    assert_that(sweep_steps).is_length(1)
    assert_that(sweep_steps[0]["env"]["TAG_PREFIX"]).is_equal_to("sha-")
    assert_that(job["permissions"]["packages"]).is_equal_to("write")


def test_every_hardened_job_that_bootstraps_uv_allows_both_release_asset_hosts() -> (
    None
):
    """Every hardened uv bootstrap must allow both release-asset hosts.

    Pinned across all workflows rather than on the smoke alone so the next
    host move is caught wherever the bootstrap runs (#2741).
    """
    jobs = _hardened_jobs_that_bootstrap_uv()
    assert_that(jobs).described_as("hardened jobs using setup-env").contains_key(
        f"{_API_SMOKE_WORKFLOW}:{_API_SMOKE_JOB}",
    )
    missing = {
        where: sorted(_UV_RELEASE_ASSET_HOSTS - _harden_runner_endpoints(job=job))
        for where, job in jobs.items()
        if not _harden_runner_endpoints(job=job) >= _UV_RELEASE_ASSET_HOSTS
    }
    assert_that(missing).described_as(
        "uv release-asset hosts missing from a hardened bootstrap job",
    ).is_empty()


def test_artifact_jobs_allowlist_the_actions_results_service() -> None:
    """Every blocked job here that moves an artifact must be able to reach it.

    The provider smoke uploads its error text and the annotate job downloads
    it; that hop is what puts the provider's own words on the tracker issue,
    so it must not depend on the results service being reachable by accident.

    Scoped to this workflow on purpose: ten pre-existing jobs elsewhere in the
    repo move artifacts under a blocked policy without naming these hosts and
    are green today, so a repo-wide rule belongs in its own change rather than
    riding along here.
    """
    offenders: list[str] = []
    for path in [_REPO_ROOT / ".github" / "workflows" / _API_SMOKE_WORKFLOW]:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(workflow, dict):
            continue
        for job_name, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or [] if isinstance(job, dict) else []
            uses = [str(step.get("uses", "")) for step in steps]
            moves_artifact = any(
                action.startswith(
                    ("actions/upload-artifact@", "actions/download-artifact@"),
                )
                for action in uses
            )
            harden = next(
                (
                    step
                    for step in steps
                    if str(step.get("uses", "")).startswith("step-security/")
                ),
                None,
            )
            if not moves_artifact or harden is None:
                continue
            with_block = harden.get("with") or {}
            if str(with_block.get("egress-policy", "")) not in {"block", "audit"}:
                continue
            allowlist = str(with_block.get("allowed-endpoints", ""))
            missing = [host for host in _ARTIFACT_ENDPOINTS if host not in allowlist]
            if missing:
                offenders.append(f"{path.name}:{job_name} missing {missing}")

    assert_that(offenders).described_as(
        "a blocked job that moves an artifact must allowlist the results service",
    ).is_empty()


def test_ghcr_cleanup_reports_its_verdict_on_main() -> None:
    """The weekly prune carries the epic's visibility contract (#2598, #2603).

    The prune failed for five weeks unseen because a scheduled failure is only
    a red X in a run list. The verdict must land on main's HEAD as a commit
    status from a job that runs on every outcome and summarises every lane
    job, and a failure must reach the shared main-failure filer under the
    lane's own label.
    """
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    jobs = cleanup["jobs"]

    report = jobs["report-status"]
    assert_that(report["needs"]).contains(*_GHCR_CLEANUP_LANE_JOBS)
    condition = _normalize_github_expr(str(report["if"]))
    assert_that(condition).contains("always()")
    assert_that(condition).contains("github.ref == 'refs/heads/main'")
    assert_that(report["permissions"]["statuses"]).is_equal_to("write")
    report_steps = [
        step
        for step in report["steps"]
        if step.get("run") == "scripts/ci/maintenance/report-workflow-commit-status.sh"
    ]
    assert_that(report_steps).is_length(1)
    # The wired script must exist: a rename or move would otherwise only fail
    # at the scheduled run's first execution after it (lintro-review P3).
    assert_that(
        (
            _REPO_ROOT
            / "scripts"
            / "ci"
            / "maintenance"
            / "report-workflow-commit-status.sh"
        ).is_file(),
    ).is_true()
    env = report_steps[0]["env"]
    assert_that(env["STATUS_CONTEXT"]).is_equal_to("ghcr-cleanup")
    # Pin the whole expression: the status must summarise every lane job, so
    # the mapping has to stay the full needs.*.result join, not just mention
    # the wildcard anywhere (lintro-review P3).
    assert_that(str(env["JOB_RESULTS"])).is_equal_to(
        "${{ join(needs.*.result, ' ') }}",
    )

    notify = jobs["notify-failure"]
    assert_that(notify["needs"]).contains(*_GHCR_CLEANUP_LANE_JOBS)
    assert_that(str(notify["uses"])).contains(
        "lgtm-hq/lgtm-ci/.github/workflows/reusable-main-failure-notifier.yml",
    )
    condition = _normalize_github_expr(str(notify["if"]))
    assert_that(condition).contains("failure()")
    assert_that(condition).contains("github.ref == 'refs/heads/main'")
    assert_that(notify["with"]["workflow-key"]).is_equal_to("ghcr-cleanup")
    labels = str(notify["with"]["failure-issue-labels"]).split(",")
    assert_that(labels).contains("ghcr-cleanup")
    assert_that(notify["permissions"]["issues"]).is_equal_to("write")


def test_number_typed_reusable_inputs_are_never_block_scalars() -> None:
    """A number input must receive a number, not a folded string (#2603).

    A ``>-`` block scalar hands the callee the *string* ``'7'`` even when the
    expression inside evaluates to a number, and GitHub rejects the reusable
    call at plan time (``Unexpected value '7'``): the job never exists, no
    log is written, and the run only shows the caller's other jobs. That is
    how both prune legs vanished from ghcr-cleanup run 34754126197. Boolean
    and string inputs tolerate the folded form, so the rule is scoped to the
    number-typed inputs of each callee.
    """
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        current_job: str | None = None
        number_keys: frozenset[str] = frozenset()
        for index, line in enumerate(lines, start=1):
            job_match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if job_match:
                current_job = job_match.group(1)
                number_keys = frozenset()
                continue
            uses_match = re.match(
                r"^    uses: lgtm-hq/lgtm-ci/\.github/workflows/([^@\s]+)@",
                line,
            )
            if uses_match:
                callee = uses_match.group(1)
                number_keys = (
                    _LGTM_CI_NUMBER_INPUTS.get(
                        callee,
                        frozenset(),
                    )
                    | _LGTM_CI_NUMBER_INPUTS_COMMON
                )
                continue
            scalar = _BLOCK_SCALAR_WITH.match(line)
            if scalar and scalar.group("key") in number_keys:
                offenders.append(
                    f"{path.name}::{current_job}::{scalar.group('key')}:{index}",
                )
    assert_that(offenders).described_as(
        "number-typed lgtm-ci inputs passed as block scalars",
    ).is_empty()
