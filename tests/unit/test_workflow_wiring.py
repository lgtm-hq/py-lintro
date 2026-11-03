"""Permission and artifact-evidence workflow wiring contracts."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from tests.unit.workflow_wiring_helpers import (
    _AI_CONTRACT_TIER1_CONTEXT as _AI_CONTRACT_TIER1_CONTEXT,
)
from tests.unit.workflow_wiring_helpers import (
    _BUILD_BINARY_WORKFLOW,
    _EVIDENCE_CALLER_IF_ALLOWLIST,
    _LOCAL_WORKFLOW_CALL_PREFIX,
    _PERMISSION_LEVELS,
    _PUBLISH_BINARIES_WORKFLOW,
    _RELEASE_IMAGE_JOBS,
    _REPO_ROOT,
    _load_workflow,
    _normalize_github_expr,
)


def _permission_level(value: object) -> int:
    """Map a workflow permission value to a comparable access level.

    Args:
        value: The raw YAML value of a single permission scope.

    Returns:
        ``0`` for none, ``1`` for read, ``2`` for write.

    Raises:
        AssertionError: If the value is not one GitHub accepts, so a typo or
            a novel level fails the walk instead of reading as "none".
    """
    if not isinstance(value, str) or value.strip().lower() not in _PERMISSION_LEVELS:
        raise AssertionError(f"unknown permission value {value!r}")
    return _PERMISSION_LEVELS[value.strip().lower()]


def test_permission_level_rejects_unknown_values() -> None:
    """A misspelt or novel permission value fails loudly, never as ``none``."""
    for bad in ("writ", None, True, 1):
        with pytest.raises(AssertionError, match="unknown permission value"):
            _permission_level(bad)


def _normalize_permissions(raw: object) -> dict[str, int] | None:
    """Normalize a ``permissions:`` value to per-scope access levels.

    Args:
        raw: A ``permissions`` mapping, the ``read-all``/``write-all``
            shorthand, or ``None`` when the block is absent.

    Returns:
        A scope-to-level mapping, or ``None`` when no block was declared.
        The ``read-all``/``write-all`` shorthands return ``{"*": level}``,
        which :func:`_granted_level` reads as a floor for every scope.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        shorthand = raw.strip().lower()
        if shorthand in {"read-all", "write-all"}:
            return {"*": _permission_level(shorthand.removesuffix("-all"))}
        return {}
    if isinstance(raw, dict):
        return {str(scope): _permission_level(value) for scope, value in raw.items()}
    return {}


def _granted_level(grant: dict[str, int], *, scope: str) -> int:
    """Return the access level ``grant`` gives ``scope``.

    Args:
        grant: A normalized permission mapping.
        scope: The permission scope being looked up.

    Returns:
        The granted level, falling back to any ``read-all``/``write-all``
        wildcard and then to ``0``.
    """
    return max(grant.get(scope, 0), grant.get("*", 0))


def _effective_grant(
    *,
    job: dict[str, Any],
    workflow: dict[str, Any],
) -> dict[str, int]:
    """Return the permissions a job actually holds.

    A job without its own ``permissions`` block inherits the workflow-level
    block. Most workflows here declare ``permissions: {}`` at the top, so the
    empty grant is the usual outcome - but not all of them do
    (``docker-build-publish.yml`` declares a top-level ``contents: read``),
    which is exactly why the workflow-level block is consulted rather than
    assumed empty. When neither the job nor the workflow declares a block at
    all, GitHub falls back to the default ``GITHUB_TOKEN`` grant, which this
    repository's org/repo setting models as ``contents: read``; that default
    is returned instead of an empty grant, while an explicit
    ``permissions: {}`` stays empty.

    Args:
        job: The parsed job mapping.
        workflow: The parsed workflow that contains ``job``.

    Returns:
        A scope-to-level mapping.
    """
    job_level = _normalize_permissions(job.get("permissions"))
    if job_level is not None:
        return job_level
    workflow_level = _normalize_permissions(workflow.get("permissions"))
    if workflow_level is not None:
        return workflow_level
    return {"contents": _PERMISSION_LEVELS["read"]}


def _local_workflow_calls(
    *,
    workflow: dict[str, Any],
) -> list[tuple[str, dict[str, Any], str]]:
    """Find the jobs of ``workflow`` that call a local reusable workflow.

    Args:
        workflow: The parsed caller workflow.

    Returns:
        ``(job id, job mapping, callee file name)`` for each local call.
    """
    calls: list[tuple[str, dict[str, Any], str]] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        uses = job.get("uses")
        if isinstance(uses, str) and uses.startswith(_LOCAL_WORKFLOW_CALL_PREFIX):
            calls.append(
                (
                    str(job_id),
                    job,
                    uses.removeprefix(_LOCAL_WORKFLOW_CALL_PREFIX),
                ),
            )
    return calls


def _permission_shortfalls(
    *,
    caller_label: str,
    caller_grant: dict[str, int],
    callee_name: str,
    depth: int,
) -> list[str]:
    """Collect every permission a callee requests beyond its caller's grant.

    Recurses one level so a callee that itself calls a local workflow is
    checked against the grant it received, not against the root caller's.

    Args:
        caller_label: Human-readable ``workflow::job`` label of the caller.
        caller_grant: The caller job's effective permissions.
        callee_name: File name of the called workflow.
        depth: Remaining recursion depth; ``0`` stops the walk.

    Returns:
        One message per scope the callee requests and the caller withholds.
    """
    callee = _load_workflow(name=callee_name)
    shortfalls: list[str] = []
    for callee_job_id, callee_job in (callee.get("jobs") or {}).items():
        if not isinstance(callee_job, dict):
            continue
        requested = _effective_grant(job=callee_job, workflow=callee)
        for scope, level in requested.items():
            if level == 0:
                continue
            granted = _granted_level(caller_grant, scope=scope)
            if granted < level:
                shortfalls.append(
                    f"{caller_label} grants {scope}="
                    f"{'none' if granted == 0 else 'read'} but "
                    f"{callee_name}::{callee_job_id} requests {scope}="
                    f"{'read' if level == 1 else 'write'}",
                )
        if depth > 0:
            nested_uses = callee_job.get("uses")
            if isinstance(nested_uses, str) and nested_uses.startswith(
                _LOCAL_WORKFLOW_CALL_PREFIX,
            ):
                shortfalls.extend(
                    _permission_shortfalls(
                        caller_label=f"{callee_name}::{callee_job_id}",
                        caller_grant=requested,
                        callee_name=nested_uses.removeprefix(
                            _LOCAL_WORKFLOW_CALL_PREFIX,
                        ),
                        depth=depth - 1,
                    ),
                )
    return shortfalls


@pytest.fixture
def workflow_files() -> list[Path]:
    """Return every workflow definition under ``.github/workflows``.

    Returns:
        Sorted paths of the repository's workflow YAML files.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    return sorted(
        path
        for path in workflows_dir.iterdir()
        if path.suffix in {".yml", ".yaml"} and path.is_file()
    )


@pytest.fixture
def parsed_workflows(workflow_files: list[Path]) -> dict[str, dict[str, Any]]:
    """Parse every workflow once, keyed by file name.

    Args:
        workflow_files: The workflow paths to parse.

    Returns:
        A mapping of file name to parsed workflow.
    """
    return {path.name: _load_workflow(name=path.name) for path in workflow_files}


def test_reusable_workflow_callers_grant_what_callees_request(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """Every local `uses:` caller grants at least what the callee requests.

    A shortfall is not a job failure but a whole-run `startup_failure` with no
    logs to point at it, so it has to be caught here. See #2484.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    shortfalls: list[str] = []
    for workflow_name, workflow in parsed_workflows.items():
        for job_id, job, callee_name in _local_workflow_calls(workflow=workflow):
            shortfalls.extend(
                _permission_shortfalls(
                    caller_label=f"{workflow_name}::{job_id}",
                    caller_grant=_effective_grant(job=job, workflow=workflow),
                    callee_name=callee_name,
                    depth=1,
                ),
            )
    assert_that(shortfalls).described_as("caller/callee permission gaps").is_empty()


def test_reusable_workflow_permission_check_covers_the_release_pipeline(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """The check actually walks the tag pipeline's reusable-workflow calls.

    An empty walk would make the test above pass vacuously, which is exactly
    how #2440's regression stayed invisible.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    calls = _local_workflow_calls(
        workflow=parsed_workflows["publish-pypi-on-tag.yml"],
    )
    callees = {callee for _, _, callee in calls}
    assert_that(callees).contains(
        _BUILD_BINARY_WORKFLOW,
        _PUBLISH_BINARIES_WORKFLOW,
        "docker-build-publish.yml",
    )
    for caller_job_id in ("build-binaries",):
        caller_job = next(job for job_id, job, _ in calls if job_id == caller_job_id)
        grant = _effective_grant(
            job=caller_job,
            workflow=parsed_workflows["publish-pypi-on-tag.yml"],
        )
        assert_that(_granted_level(grant, scope="actions")).described_as(
            caller_job_id,
        ).is_greater_than_or_equal_to(_PERMISSION_LEVELS["read"])


def test_permission_shortfalls_detects_a_withheld_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_permission_shortfalls` reports a scope the caller withholds.

    Guards the detector itself against a synthetic callee: without this, a
    detector that always returned an empty list would leave the walk above
    green forever.

    Args:
        monkeypatch: pytest attribute patcher, used to substitute a synthetic
            callee workflow for the on-disk one.
    """
    callee = {
        "permissions": {},
        "jobs": {
            "compile": {"permissions": {"contents": "write", "actions": "read"}},
        },
    }
    monkeypatch.setattr(
        f"{__name__}._load_workflow",
        lambda *, name: callee,
    )
    shortfalls = _permission_shortfalls(
        caller_label="caller.yml::calls",
        caller_grant=_normalize_permissions({"contents": "write"}) or {},
        callee_name="callee.yml",
        depth=1,
    )
    assert_that(shortfalls).is_length(1)
    assert_that(shortfalls[0]).contains("actions=none")
    assert_that(shortfalls[0]).contains("requests actions=read")


def test_permission_shortfalls_detects_a_read_grant_against_a_write_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller granting read against a write request is reported as a gap.

    The withheld-scope test above only exercises the ``none``/``read`` corner
    of the message. The ``read``/``write`` rendering is the shape a callee
    bumping a scope to write produces, and it is the one that startup-fails a
    tag run, so it needs its own synthetic case.

    Args:
        monkeypatch: pytest attribute patcher, used to substitute a synthetic
            callee workflow for the on-disk one.
    """
    callee = {
        "permissions": {},
        "jobs": {
            "compile": {"permissions": {"actions": "write"}},
        },
    }
    monkeypatch.setattr(
        f"{__name__}._load_workflow",
        lambda *, name: callee,
    )
    shortfalls = _permission_shortfalls(
        caller_label="caller.yml::calls",
        caller_grant=_normalize_permissions({"actions": "read"}) or {},
        callee_name="callee.yml",
        depth=1,
    )
    assert_that(shortfalls).is_length(1)
    assert_that(shortfalls[0]).contains("grants actions=read")
    assert_that(shortfalls[0]).contains("requests actions=write")


def test_reusable_workflow_walk_fails_on_the_pre_2518_publish_workflow(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """The walk reddens on the exact grant the v0.151.2-v0.152.6 tags died on.

    #2440 gave build-binary.yml's compile jobs ``actions: read`` while the
    ``homebrew-tap`` caller still granted only ``contents: write``; #2518 added
    the grant. Since #2562 the compile jobs live in build-binaries.yml behind
    the ``build-binaries`` caller, which carries that ``actions: read`` for
    the reuse check. Replaying the withheld grant against the real callee
    proves the walk catches it rather than passing because nothing on disk is
    broken today.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    publish = deepcopy(parsed_workflows["publish-pypi-on-tag.yml"])
    caller_job = publish["jobs"]["build-binaries"]
    pre_2518_grant = {
        scope: value
        for scope, value in caller_job["permissions"].items()
        if scope != "actions"
    }
    assert_that(caller_job["permissions"]).described_as(
        "the fixture only means something while #2518's grant is present",
    ).contains_key("actions")
    caller_job["permissions"] = pre_2518_grant

    shortfalls = _permission_shortfalls(
        caller_label="publish-pypi-on-tag.yml::build-binaries",
        caller_grant=_effective_grant(job=caller_job, workflow=publish),
        callee_name=str(caller_job["uses"]).removeprefix(
            _LOCAL_WORKFLOW_CALL_PREFIX,
        ),
        depth=1,
    )
    assert_that(shortfalls).is_not_empty()
    for message in shortfalls:
        assert_that(message).contains("requests actions=read")
    assert_that(" ".join(shortfalls)).contains(
        f"{_BUILD_BINARY_WORKFLOW}::build-macos",
    )


def test_permission_shortfalls_is_silent_when_the_grant_covers_the_callee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller granting at least as much as the callee produces no message.

    Args:
        monkeypatch: pytest attribute patcher.
    """
    callee = {
        "permissions": {"contents": "read"},
        "jobs": {"compile": {"permissions": {"actions": "read"}}, "docs": {}},
    }
    monkeypatch.setattr(
        f"{__name__}._load_workflow",
        lambda *, name: callee,
    )
    shortfalls = _permission_shortfalls(
        caller_label="caller.yml::calls",
        caller_grant=_normalize_permissions(
            {"contents": "write", "actions": "read"},
        )
        or {},
        callee_name="callee.yml",
        depth=1,
    )
    assert_that(shortfalls).is_empty()


def test_permission_shortfalls_recurses_into_a_nested_local_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grandchild's request is checked against the grant its caller received.

    Args:
        monkeypatch: pytest attribute patcher.
    """
    workflows = {
        "middle.yml": {
            "permissions": {},
            "jobs": {
                "relay": {
                    "permissions": {"contents": "write"},
                    "uses": "./.github/workflows/leaf.yml",
                },
            },
        },
        "leaf.yml": {
            "permissions": {},
            "jobs": {"compile": {"permissions": {"packages": "write"}}},
        },
    }
    monkeypatch.setattr(
        f"{__name__}._load_workflow",
        lambda *, name: workflows[name],
    )
    shortfalls = _permission_shortfalls(
        caller_label="root.yml::calls",
        caller_grant=_normalize_permissions({"contents": "write"}) or {},
        callee_name="middle.yml",
        depth=1,
    )
    assert_that(shortfalls).is_length(1)
    assert_that(shortfalls[0]).contains("middle.yml::relay")
    assert_that(shortfalls[0]).contains("requests packages=write")


def test_missing_permissions_block_models_the_token_default() -> None:
    """No block anywhere means GitHub's default grant, not an empty one."""
    default_grant = _effective_grant(job={}, workflow={})
    assert_that(_granted_level(default_grant, scope="contents")).is_equal_to(
        _PERMISSION_LEVELS["read"],
    )
    assert_that(_granted_level(default_grant, scope="packages")).is_equal_to(0)

    explicit_empty = _effective_grant(job={}, workflow={"permissions": {}})
    assert_that(explicit_empty).is_equal_to({})
    inherited = _effective_grant(
        job={},
        workflow={"permissions": {"contents": "read"}},
    )
    assert_that(inherited).is_equal_to({"contents": _PERMISSION_LEVELS["read"]})
    job_overrides = _effective_grant(
        job={"permissions": {}},
        workflow={"permissions": {"contents": "write"}},
    )
    assert_that(job_overrides).is_equal_to({})


def test_permission_shorthands_normalize_to_levels() -> None:
    """``read-all``/``write-all`` and a missing block normalize correctly."""
    assert_that(_normalize_permissions(None)).is_none()
    assert_that(_normalize_permissions({})).is_equal_to({})
    read_all = _normalize_permissions("read-all") or {}
    assert_that(_granted_level(read_all, scope="actions")).is_equal_to(1)
    write_all = _normalize_permissions("write-all") or {}
    assert_that(_granted_level(write_all, scope="packages")).is_equal_to(2)
    explicit = _normalize_permissions({"contents": "read", "id-token": "write"}) or {}
    assert_that(_granted_level(explicit, scope="contents")).is_equal_to(1)
    assert_that(_granted_level(explicit, scope="id-token")).is_equal_to(2)
    assert_that(_granted_level(explicit, scope="actions")).is_equal_to(0)


def test_every_pushed_reusable_docker_call_carries_provenance_and_sbom(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """Release images carry the same evidence as the tool images (#2630).

    ``reusable-docker.yml`` gates its GitHub attestation on ``provenance``, so
    an opt-out drops the attestation as well as the BuildKit provenance. Every
    call that can push must therefore pass both flags; the backfill dispatch
    shares the release jobs, so it is covered by the same assertion.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    missing: dict[str, dict[str, Any]] = {}
    for workflow_name, workflow in parsed_workflows.items():
        for job_id, job in (workflow.get("jobs") or {}).items():
            uses = str(job.get("uses", ""))
            if "reusable-docker.yml" not in uses:
                continue
            with_block = job.get("with") or {}
            push = with_block.get("push", "")
            # YAML ``push: false`` parses to a boolean; only an explicit false
            # or an absent input marks a validate-only job.
            if push is False or str(push).strip().lower() in ("", "false"):
                continue
            evidence = {
                key: with_block.get(key)
                for key in ("provenance", "sbom", "cosign-sign", "scan")
            }
            if not all(evidence[key] is True for key in evidence):
                missing[f"{workflow_name}::{job_id}"] = evidence
            # Execution bypasses defeat the evidence inputs: a
            # ``continue-on-error`` job succeeds past an attestation failure,
            # and a constant-false ``if`` skips production entirely while
            # every ``with`` assertion above still passes.
            assert_that(job.get("continue-on-error")).described_as(
                f"{workflow_name}::{job_id} continue-on-error",
            ).is_none()
            condition = str(job.get("if", "")).strip()
            if condition:
                assert_that(condition.lower()).described_as(
                    f"{workflow_name}::{job_id} if",
                ).is_not_in(("false", "${{ false }}"))
    assert_that(missing).described_as("pushed image jobs lacking evidence").is_empty()

    publish = parsed_workflows["docker-build-publish.yml"]
    for job_id in _RELEASE_IMAGE_JOBS:
        with_block = publish["jobs"][job_id]["with"]
        assert_that(str(with_block["scan-exit-code"])).described_as(job_id).is_equal_to(
            "0",
        )


def test_pushed_docker_callers_run_under_allowlisted_guards(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """A pushed reusable-docker call runs only under a reviewed guard (#2630).

    The bypass guards in the provenance test reject constant-false ``if``
    values outright; this pins the full non-constant condition of every
    pushed caller against an explicit allowlist, so a new guard that skips
    evidence production cannot slip in unreviewed, and rejects any caller
    that is missing from the allowlist entirely.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    seen: set[str] = set()
    for workflow_name, workflow in parsed_workflows.items():
        for job_id, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            if "reusable-docker.yml" not in str(job.get("uses", "")):
                continue
            push = (job.get("with") or {}).get("push", "")
            if push is False or str(push).strip().lower() in ("", "false"):
                continue
            key = f"{workflow_name}::{job_id}"
            seen.add(key)
            assert_that(
                _normalize_github_expr(str(job.get("if", ""))),
            ).described_as(f"{key} if").is_equal_to(
                _normalize_github_expr(_EVIDENCE_CALLER_IF_ALLOWLIST.get(key, "")),
            )
    assert_that(seen).described_as(
        "pushed reusable-docker callers vs the guard allowlist",
    ).is_equal_to(set(_EVIDENCE_CALLER_IF_ALLOWLIST))


def test_build_stage_never_holds_contents_write() -> None:
    """No job in the build stage can write to the repository or a release.

    The point of the split (#2562) is that a binary is built, verified and
    attested by jobs that cannot publish it. Since PR (c) the only
    ``contents: write`` in the tag pipeline is the github-release call; no
    job of either binary workflow holds it.
    """
    build = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    assert_that(build["permissions"]).is_equal_to({})
    for job_id, job in build["jobs"].items():
        grant = _effective_grant(job=job, workflow=build)
        assert_that(_granted_level(grant, scope="contents")).described_as(
            job_id,
        ).is_less_than(_PERMISSION_LEVELS["write"])

    publish = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    assert_that(publish["permissions"]).is_equal_to({})
    writers = {
        job_id
        for job_id, job in publish["jobs"].items()
        if _granted_level(_effective_grant(job=job, workflow=publish), scope="contents")
        >= _PERMISSION_LEVELS["write"]
    }
    assert_that(writers).is_empty()


def test_tag_pipeline_calls_the_mirror_after_the_github_release() -> None:
    """The tag pipeline is what fires the mirror bump, after the release job.

    Pins the whole repair from #2599: a caller job exists, it waits for the
    release the mirror mirrors, it passes the pushed tag, it hands over the
    cross-repo token, and it grants at least what the callee's job requests
    (a shortfall is a logless ``startup_failure``; see #2484/#2563).
    """
    caller = _load_workflow(name="publish-pypi-on-tag.yml")
    callee = _load_workflow(name="mirror-release.yml")
    job = caller["jobs"]["mirror-release"]

    assert_that(job["uses"]).is_equal_to("./.github/workflows/mirror-release.yml")
    assert_that(job["needs"]).contains("github-release")
    assert_that(job["with"]["release_tag"]).is_equal_to("${{ github.ref_name }}")
    assert_that(job["secrets"]["MIRROR_APP_ID"]).is_equal_to(
        "${{ secrets.MIRROR_APP_ID }}",
    )
    assert_that(job["secrets"]["MIRROR_APP_PRIVATE_KEY"]).is_equal_to(
        "${{ secrets.MIRROR_APP_PRIVATE_KEY }}",
    )
    # `secrets: inherit` would hand the call every org/repo secret.
    assert_that(job["secrets"]).is_instance_of(dict)

    granted = _effective_grant(job=job, workflow=caller)
    for callee_job in callee["jobs"].values():
        requested = _effective_grant(job=callee_job, workflow=callee)
        for scope, level in requested.items():
            assert_that(_granted_level(granted, scope=scope)).described_as(
                f"caller grant for {scope}",
            ).is_greater_than_or_equal_to(level)
