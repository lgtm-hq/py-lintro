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
    "action_ref",
    [
        "step-security/harden-runner@bf7454d06d71f1098171f2acdf0cd4708d7b5920",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    ],
)
def test_workflow_pins_actions_to_sha(*, action_ref: str) -> None:
    """Third-party actions are pinned to full commit SHAs.

    Args:
        action_ref: The expected ``owner/repo@sha`` reference.
    """
    body = WORKFLOW.read_text(encoding="utf-8")

    assert_that(body).contains(action_ref)
