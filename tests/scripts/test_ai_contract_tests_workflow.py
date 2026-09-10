"""Structural tests for the tiered AI CLI contract workflow (#1614).

The workflow's value depends entirely on properties a reader cannot see at a
glance, and each of them has bitten this repo before:

* **No path filter.** A path-filtered required check never reports on the PRs it
  filters out, and those PRs then wait forever in the merge queue for a check that
  will never arrive (#1196).
* **One pin site.** The image digest is read from the root Dockerfile rather than
  copied, so the gate can never verify a different image than the one users get.
* **Tier separation.** The quota-spending tier must not run on pull requests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from assertpy import assert_that

from tests.scripts._action_pins import action_pin, actions_used_in

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ai-contract-tests.yml"
RUNNER = REPO_ROOT / "scripts" / "ci" / "run-ai-contract-tests.sh"
PIN_SCRIPT = REPO_ROOT / "scripts" / "ci" / "ai_tools_image_pin.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"

TIER1_JOB = "tier1-flag-surface"
TIER2_JOB = "tier2-invocation-smoke"


@pytest.fixture
def workflow() -> Any:
    """Return the parsed contract-tests workflow.

    Returns:
        The parsed workflow mapping.
    """
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _load_pin_module() -> ModuleType:
    """Load the image-pin resolver as an importable module.

    Returns:
        The loaded module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location("ai_tools_image_pin", PIN_SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {PIN_SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ai_tools_image_pin"] = module
    spec.loader.exec_module(module)
    return module


# --- trigger shape -----------------------------------------------------------


def test_tier1_runs_on_every_pull_request(workflow: Any) -> None:
    """No path filter, so the gate can safely be made required (#1196).

    Args:
        workflow: The parsed workflow mapping.
    """
    trigger = workflow[True] if True in workflow else workflow["on"]

    assert_that(trigger).contains_key("pull_request")
    assert_that(trigger["pull_request"]).does_not_contain_key("paths")
    assert_that(trigger["pull_request"]).does_not_contain_key("paths-ignore")


def test_tier1_is_not_gated_on_the_event_type(workflow: Any) -> None:
    """Tier 1 must run for every trigger, including the weekly schedule.

    Args:
        workflow: The parsed workflow mapping.
    """
    assert_that(workflow["jobs"][TIER1_JOB]).does_not_contain_key("if")


def test_tier2_never_runs_on_a_pull_request(workflow: Any) -> None:
    """Real invocations spend quota, so they stay off the PR hot path.

    Args:
        workflow: The parsed workflow mapping.
    """
    # Exact, not substring: an added `|| github.event_name == 'push'` would slip
    # past independent contains() checks while widening what spends quota.
    assert_that(workflow["jobs"][TIER2_JOB]["if"]).is_equal_to(
        "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'",
    )


def test_tier2_waits_on_the_free_tier(workflow: Any) -> None:
    """No point spending quota to learn what ``--help`` already proved.

    Args:
        workflow: The parsed workflow mapping.
    """
    assert_that(workflow["jobs"][TIER2_JOB]["needs"]).contains(TIER1_JOB)


def test_neither_tier_swallows_its_own_failure(workflow: Any) -> None:
    """A contract gate with continue-on-error is not a gate.

    Args:
        workflow: The parsed workflow mapping.
    """
    for name, job in workflow["jobs"].items():
        assert_that(job).described_as(name).does_not_contain_key("continue-on-error")
        for step in job["steps"]:
            assert_that(step).described_as(
                f"{name} / {step.get('name')}",
            ).does_not_contain_key("continue-on-error")


def test_both_tiers_are_bounded_by_a_timeout(workflow: Any) -> None:
    """An unbounded job hangs until the runner limit instead of failing fast.

    Args:
        workflow: The parsed workflow mapping.
    """
    for name, job in workflow["jobs"].items():
        assert_that(job).described_as(name).contains_key("timeout-minutes")


# --- image pinning -----------------------------------------------------------


def test_workflow_resolves_the_image_instead_of_copying_the_digest(
    workflow: Any,
) -> None:
    """A second copy of the digest would drift from the Dockerfile's pin.

    Args:
        workflow: The parsed workflow mapping.
    """
    body = WORKFLOW.read_text(encoding="utf-8")

    assert_that(body).contains("scripts/ci/ai_tools_image_pin.py")
    assert_that(body).described_as(
        "the digest must live only in the Dockerfile",
    ).does_not_contain("lintro-ai-tools:latest@sha256:")

    for name in (TIER1_JOB, TIER2_JOB):
        steps = workflow["jobs"][name]["steps"]
        resolvers = [
            step
            for step in steps
            if "ai_tools_image_pin.py" in str(step.get("run", ""))
        ]
        assert_that(resolvers).described_as(name).is_length(1)


def test_pin_resolver_matches_the_dockerfile_ai_stage() -> None:
    """The resolver returns exactly what the ``aitools`` stage is pinned to."""
    module = _load_pin_module()

    resolved = module.resolve_image(
        dockerfile_text=DOCKERFILE.read_text(encoding="utf-8"),
        stage="aitools",
    )

    assert_that(resolved).starts_with("ghcr.io/lgtm-hq/lintro-ai-tools")
    assert_that(resolved).contains("@sha256:")
    assert_that(DOCKERFILE.read_text(encoding="utf-8")).contains(resolved)


def test_pin_resolver_rejects_an_unpinned_base() -> None:
    """A floating tag would silently change what the gate verifies."""
    module = _load_pin_module()

    with pytest.raises(ValueError, match="not digest-pinned"):
        module.resolve_image(
            dockerfile_text="FROM ghcr.io/lgtm-hq/lintro-ai-tools:latest AS aitools\n",
            stage="aitools",
        )


def test_pin_resolver_rejects_a_missing_stage() -> None:
    """A renamed stage must fail loudly rather than yield an empty image."""
    module = _load_pin_module()

    with pytest.raises(ValueError, match="no `FROM ... AS aitools` stage"):
        module.resolve_image(dockerfile_text="FROM scratch\n", stage="aitools")


# --- runner script -----------------------------------------------------------


def test_runner_requires_binaries_so_a_broken_gate_cannot_skip() -> None:
    """Inside the baked image, a missing CLI is a bug, not a developer's absence."""
    body = RUNNER.read_text(encoding="utf-8")

    assert_that(body).contains("LINTRO_CONTRACT_REQUIRE_BINARIES=1")


def test_runner_selects_the_tier_by_pytest_marker() -> None:
    """Each tier runs its own marker, so tier 2 cannot leak into the PR gate."""
    body = RUNNER.read_text(encoding="utf-8")

    assert_that(body).contains("contract_tier1")
    assert_that(body).contains("contract_tier2")
    assert_that(body).contains("LINTRO_CONTRACT_TIER2=1")


#: Every variable the tier-2 branch forwards into the contract container, in
#: the script's own order. Forwarded by name, so the value never appears in the
#: argv — docker reads it from the caller's environment.
FORWARDED_TIER2_ENV = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CODEX_API_KEY",
    "CURSOR_API_KEY",
    "LINTRO_CLI_BARE",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "DISABLE_AUTOUPDATER",
)

#: Where the restored Codex session is mounted, and the variable that names it.
#: Deliberately outside the container's HOME (/tmp): codex refuses to create its
#: PATH-alias helper binaries when CODEX_HOME sits under a temporary directory.
CODEX_MOUNT_TARGET = "/opt/codex-home"

#: Stand-in credential value. Distinctive so the argv assertions can prove the
#: value never leaves the environment, and not a literal at a token-named dict
#: key, which bandit reads as a hardcoded secret.
CREDENTIAL_SENTINEL = "sentinel-value"


def _runner_docker_args(*, env: dict[str, str]) -> list[str]:
    """Return the docker argv the runner would build for this environment.

    Uses the script's print-args hook rather than Docker, so the argv
    construction — the codex mount and the credential forwarding loop, neither
    of which is visible in a workflow diff — is exercised without a daemon, an
    image pull, or a provider credential.

    Args:
        env: The environment the script runs under. ``PATH`` and the print
            hook are added; nothing else leaks in from the test process.

    Returns:
        The docker argv, one element per line of output.
    """
    import os
    import subprocess  # nosec B404 - runs the repo's own script with a fixed argv

    result = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        [str(RUNNER)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ.get("PATH", ""),
            "LINTRO_CONTRACT_PRINT_DOCKER_ARGS": "1",
            **env,
        },
    )

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    return result.stdout.splitlines()


def test_runner_forwards_only_the_credentials_the_caller_actually_set() -> None:
    """A credential must reach the container by name, and only when set.

    Forwarding an unset variable with a default would hand the suite an empty
    credential that looks present, which is exactly the silent pass the
    contract tiers exist to prevent.
    """
    provided = ("CLAUDE_CODE_OAUTH_TOKEN", "CURSOR_API_KEY")
    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "2",
            "HOME": "/nonexistent",
            **dict.fromkeys(provided, CREDENTIAL_SENTINEL),
        },
    )

    assert_that(args).contains(*provided)
    # By name only: the credential's value must not be written into the argv,
    # which `ps` and any command echo would expose.
    assert_that(args).does_not_contain(CREDENTIAL_SENTINEL)
    for name in FORWARDED_TIER2_ENV:
        if name in provided:
            continue
        assert_that(args).described_as(name).does_not_contain(name)


def test_every_tier2_workflow_credential_is_forwarded_into_the_container(
    workflow: Any,
) -> None:
    """A credential the workflow injects must actually reach the container.

    The two halves live in different files: the workflow decides which
    credentials Tier 2 gets, and the runner decides which variables cross into
    the container. A credential added to the workflow but missed in the
    forwarding loop is silently dropped — the suite then reports the lane as
    unauthenticated with nothing pointing at the omission. Derive the names
    from the workflow and prove the script forwards each one.

    Args:
        workflow: The parsed workflow mapping.
    """
    step = next(
        step
        for step in workflow["jobs"][TIER2_JOB]["steps"]
        if "run-ai-contract-tests.sh" in str(step.get("run", ""))
    )
    # `secrets.` catches the provider credentials; the gateway base URL is a
    # repo variable rather than a secret but is just as load-bearing — without
    # it the gateway token authenticates against the wrong host.
    credentials = sorted(
        name
        for name, value in step["env"].items()
        if "secrets." in str(value) or "vars.ZAI_BASE_URL" in str(value)
    )

    assert_that(credentials).described_as("tier-2 workflow credentials").is_not_empty()
    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "2",
            "HOME": "/nonexistent",
            **dict.fromkeys(credentials, CREDENTIAL_SENTINEL),
        },
    )

    assert_that(args).described_as("forwarded by the script").contains(*credentials)
    assert_that(FORWARDED_TIER2_ENV).described_as(
        "declared in the forwarding list",
    ).contains(*credentials)


def test_runner_forwards_every_declared_tier2_credential() -> None:
    """The whole forwarding list is live, not just the two lanes under test."""
    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "2",
            "HOME": "/nonexistent",
            **dict.fromkeys(FORWARDED_TIER2_ENV, CREDENTIAL_SENTINEL),
        },
    )

    assert_that(args).contains(*FORWARDED_TIER2_ENV)


def test_runner_forwards_no_credentials_on_the_free_tier() -> None:
    """Tier 1 needs no credential, so none may reach the container."""
    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "1",
            "HOME": "/nonexistent",
            **dict.fromkeys(FORWARDED_TIER2_ENV, CREDENTIAL_SENTINEL),
        },
    )

    for name in FORWARDED_TIER2_ENV:
        assert_that(args).described_as(name).does_not_contain(name)


def _codex_session(root: Path) -> Path:
    """Create a restored codex session under a stand-in home.

    Args:
        root: The stand-in home directory.

    Returns:
        The session directory holding an auth.json.
    """
    session = root / ".codex"
    session.mkdir()
    (session / "auth.json").write_text("{}", encoding="utf-8")
    return session


def test_runner_mounts_a_restored_codex_session_outside_the_container_home(
    tmp_path: Path,
) -> None:
    """On a runner the restored session itself is mounted, named by CODEX_HOME.

    Args:
        tmp_path: Stand-in for the runner's restored session directory.
    """
    session = _codex_session(tmp_path)

    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "2",
            "HOME": str(tmp_path),
            "GITHUB_ACTIONS": "true",
        },
    )

    assert_that(args).contains(f"{session}:{CODEX_MOUNT_TARGET}")
    assert_that(args).contains(f"CODEX_HOME={CODEX_MOUNT_TARGET}")
    assert_that(CODEX_MOUNT_TARGET.startswith("/tmp")).described_as(
        "codex refuses a CODEX_HOME under the container's temporary HOME",
    ).is_false()


def test_runner_skips_the_codex_mount_without_a_restored_session(
    tmp_path: Path,
) -> None:
    """An unrestored session must not be mounted as if it were there.

    Args:
        tmp_path: An empty stand-in home with no session in it.
    """
    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "2",
            "HOME": str(tmp_path),
        },
    )

    assert_that(args).does_not_contain(f"CODEX_HOME={CODEX_MOUNT_TARGET}")
    assert_that([arg for arg in args if CODEX_MOUNT_TARGET in arg]).is_empty()


def test_runner_honours_an_explicit_codex_session_dir(tmp_path: Path) -> None:
    """CODEX_SESSION_DIR overrides the default $HOME/.codex location.

    Args:
        tmp_path: Holds the override directory and an unused default home.
    """
    override = tmp_path / "elsewhere"
    override.mkdir()
    (override / "auth.json").write_text("{}", encoding="utf-8")

    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "2",
            "HOME": "/nonexistent",
            "CODEX_SESSION_DIR": str(override),
        },
    )

    assert_that(args).contains(f"{override}:{CODEX_MOUNT_TARGET}")


def test_runner_skips_the_mount_when_an_explicit_dir_holds_no_session(
    tmp_path: Path,
) -> None:
    """An override is still gated on the session actually being there.

    ``CODEX_SESSION_DIR`` names where to look, not a promise that the restore
    succeeded; mounting an empty directory would hand codex a CODEX_HOME with
    no auth.json and turn a missing credential into a confusing CLI error.

    Args:
        tmp_path: An existing but empty override directory.
    """
    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "2",
            "HOME": "/nonexistent",
            "CODEX_SESSION_DIR": str(tmp_path),
        },
    )

    assert_that(args).does_not_contain(f"CODEX_HOME={CODEX_MOUNT_TARGET}")
    assert_that([arg for arg in args if CODEX_MOUNT_TARGET in arg]).is_empty()


def test_runner_never_mounts_a_session_on_the_free_tier(tmp_path: Path) -> None:
    """Tier 1 runs help probes only, so a session must not reach it.

    The mount lives inside the tier-2 branch. Tier 1 spends no quota and needs
    no credential, and handing it one would widen what a cheap always-on gate
    can touch.

    Args:
        tmp_path: Stand-in home holding a restored session.
    """
    _codex_session(tmp_path)

    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "1",
            "HOME": str(tmp_path),
            "GITHUB_ACTIONS": "true",
        },
    )

    assert_that(args).does_not_contain(f"CODEX_HOME={CODEX_MOUNT_TARGET}")
    assert_that([arg for arg in args if CODEX_MOUNT_TARGET in arg]).is_empty()


def test_runner_copies_the_default_session_for_a_local_run(tmp_path: Path) -> None:
    """Off a runner, the caller's own codex login must not be the mount source.

    The container writes as root and codex may refresh the session in place, so
    mounting a developer's real ``$HOME/.codex`` read-write could leave it
    root-owned and their ``codex`` logged out. A local run gets a disposable
    copy; the CI path (asserted above) is unchanged.

    Args:
        tmp_path: Stand-in home holding the developer's session.
    """
    session = _codex_session(tmp_path)

    args = _runner_docker_args(
        env={
            "IMAGE": "example.invalid/img@sha256:0",
            "TIER": "2",
            "HOME": str(tmp_path),
        },
    )

    mounts = [arg for arg in args if arg.endswith(f":{CODEX_MOUNT_TARGET}")]
    assert_that(mounts).described_as("the session is still mounted").is_length(1)
    assert_that(args).contains(f"CODEX_HOME={CODEX_MOUNT_TARGET}")
    assert_that(mounts[0]).described_as(
        "a local run must not mount the caller's own session directory",
    ).is_not_equal_to(f"{session}:{CODEX_MOUNT_TARGET}")


def test_runner_help_exits_zero() -> None:
    """The runner documents itself without needing Docker."""
    import subprocess  # nosec B404 - runs the repo's own script with a fixed argv

    result = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        [str(RUNNER), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


@pytest.mark.parametrize(
    "action",
    [
        "step-security/harden-runner",
        "actions/checkout",
    ],
)
def test_workflow_uses_pinned_actions(*, action: str) -> None:
    """The workflow hardens the runner and checks out, both at pinned SHAs.

    Membership is what this asserts; the pin itself is derived from the
    workflow files (#2432) and its shape is enforced by
    ``pinned_action_shas`` rather than by a literal repeated here.

    Args:
        action: The ``owner/repo`` identifier expected in the workflow.
    """
    assert_that(actions_used_in(WORKFLOW)).contains(action)
    assert_that(WORKFLOW.read_text(encoding="utf-8")).contains(action_pin(action))
