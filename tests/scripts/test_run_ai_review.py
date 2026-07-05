"""Tests for the dogfood AI review CI helpers.

Covers the ``enable_review_config.py`` config patcher, the graceful-skip
behaviour of ``run-ai-review.sh``, the review CLI flags the script relies on,
and that the ``ai-review.yml`` workflow parses as valid YAML.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_SCRIPT = REPO_ROOT / "scripts" / "ci" / "enable_review_config.py"
SHELL_SCRIPT = REPO_ROOT / "scripts" / "ci" / "run-ai-review.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ai-review.yml"


def _load_config_module() -> ModuleType:
    """Load enable_review_config.py as an importable module.

    Returns:
        The loaded module exposing its public helpers.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location(
        "enable_review_config",
        CONFIG_SCRIPT,
    )
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {CONFIG_SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["enable_review_config"] = module
    spec.loader.exec_module(module)
    return module


def _run_shell(
    *,
    args: list[str],
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run the shell helper with a controlled environment.

    Args:
        args: Positional arguments passed to the script.
        env_overrides: Environment variables layered onto a minimal base.

    Returns:
        The completed subprocess result.
    """
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(env_overrides)
    return subprocess.run(
        [str(SHELL_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_resolve_max_cost_defaults_when_unset() -> None:
    """An unset or blank cost value falls back to the default cap."""
    module = _load_config_module()

    assert_that(module.resolve_max_cost_usd(raw_value=None)).is_equal_to(
        module.DEFAULT_MAX_COST_USD,
    )
    assert_that(module.resolve_max_cost_usd(raw_value="  ")).is_equal_to(
        module.DEFAULT_MAX_COST_USD,
    )


def test_resolve_max_cost_parses_value() -> None:
    """A valid numeric string parses into a float cap."""
    module = _load_config_module()

    assert_that(module.resolve_max_cost_usd(raw_value="1.25")).is_equal_to(1.25)


def test_resolve_max_cost_rejects_negative() -> None:
    """A negative cost value raises ValueError."""
    module = _load_config_module()

    assert_that(module.resolve_max_cost_usd).raises(ValueError).when_called_with(
        raw_value="-1",
    )


def test_patch_config_enables_ai_and_bounds_cost() -> None:
    """Patching enables AI, pins transport/provider, and sets the cost cap."""
    module = _load_config_module()

    data = {"ai": {"enabled": False, "model": "keep-me"}, "review": {"depth": 1}}
    patched = module.patch_config(data=data, max_cost_usd=0.5)

    assert_that(patched["ai"]["enabled"]).is_true()
    assert_that(patched["ai"]["transport"]).is_equal_to("api")
    assert_that(patched["ai"]["provider"]).is_equal_to("anthropic")
    assert_that(patched["ai"]["max_cost_usd"]).is_equal_to(0.5)
    # Unrelated values are preserved.
    assert_that(patched["ai"]["model"]).is_equal_to("keep-me")
    assert_that(patched["review"]["depth"]).is_equal_to(1)


def test_patch_config_creates_ai_section_when_missing() -> None:
    """A missing ai section is created rather than raising."""
    module = _load_config_module()

    patched = module.patch_config(data={}, max_cost_usd=0.25)

    assert_that(patched["ai"]["enabled"]).is_true()
    assert_that(patched["ai"]["max_cost_usd"]).is_equal_to(0.25)


def test_main_patches_config_file(tmp_path: Path) -> None:
    """main() writes the enabled AI settings back to the target file."""
    module = _load_config_module()
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("ai:\n  enabled: false\n", encoding="utf-8")

    exit_code = module.main(argv=["--config", str(config_file)])

    assert_that(exit_code).is_equal_to(0)
    reloaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert_that(reloaded["ai"]["enabled"]).is_true()
    assert_that(reloaded["ai"]["transport"]).is_equal_to("api")


def test_main_returns_error_when_config_missing(tmp_path: Path) -> None:
    """main() returns a non-zero code when the config file is absent."""
    module = _load_config_module()

    exit_code = module.main(argv=["--config", str(tmp_path / "missing.yaml")])

    assert_that(exit_code).is_equal_to(1)


def test_shell_help_exits_zero() -> None:
    """The --help flag prints usage and exits 0."""
    result = _run_shell(args=["--help"], env_overrides={})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_shell_skips_without_api_key() -> None:
    """An empty ANTHROPIC_API_KEY skips gracefully with exit 0."""
    result = _run_shell(
        args=[],
        env_overrides={"ANTHROPIC_API_KEY": "", "PR_NUMBER": "123"},
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("skipping AI review")


def test_shell_skips_without_pr_number() -> None:
    """A configured key but no PR number skips gracefully with exit 0."""
    result = _run_shell(
        args=[],
        env_overrides={"ANTHROPIC_API_KEY": "dummy-key", "PR_NUMBER": ""},
    )

    assert_that(result.returncode).is_equal_to(0)


def test_review_cli_accepts_script_flags() -> None:
    """The review command exposes the flags the script invokes."""
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--pr")
    assert_that(result.output).contains("--depth")
    assert_that(result.output).contains("--output")
    assert_that(result.output).contains("json")


def test_workflow_yaml_parses() -> None:
    """The ai-review workflow is valid YAML with the expected trigger."""
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    assert_that(loaded).contains_key("jobs")
    assert_that(loaded["jobs"]).contains_key("ai-review")
    trigger = loaded[True] if True in loaded else loaded["on"]
    assert_that(trigger).contains_key("pull_request")


def test_workflow_job_is_non_blocking() -> None:
    """The review job is non-blocking at the job level.

    Job-level ``continue-on-error`` keeps setup failures (uv sync, egress,
    checkout) from turning the PR check red, matching the informational intent.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    job = loaded["jobs"]["ai-review"]
    assert_that(job).contains_key("continue-on-error")
    assert_that(job["continue-on-error"]).is_true()


def test_workflow_job_is_same_repo_only() -> None:
    """The keyed job only runs for same-repository (non-fork) PRs.

    The job ``if`` guard combines the draft check with a head-repo equality
    check so fork PRs never attempt the job that has the secret in scope.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    guard = loaded["jobs"]["ai-review"]["if"]
    assert_that(guard).contains("github.event.pull_request.draft == false")
    assert_that(guard).contains(
        "github.event.pull_request.head.repo.full_name == github.repository",
    )


def test_workflow_installs_from_base_ref_not_pr_head() -> None:
    """Lintro is installed from the trusted base ref, never the PR head.

    The checkout used for the keyed install must pin ``ref`` to the PR's base
    SHA so PR-controlled code never executes with the ANTHROPIC_API_KEY in
    scope. The PR itself is still reviewed via ``gh`` (diff fetched over the
    API), independent of the checked-out tree.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    steps = loaded["jobs"]["ai-review"]["steps"]
    checkout_steps = [
        step
        for step in steps
        if isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/checkout@")
    ]
    assert_that(checkout_steps).is_length(1)

    checkout = checkout_steps[0]
    assert_that(checkout).contains_key("with")
    assert_that(checkout["with"]["ref"]).is_equal_to(
        "${{ github.event.pull_request.base.sha }}",
    )
    # The PR head is never checked out for the keyed install.
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert_that(workflow_text).does_not_contain("pull_request.head.sha")
    assert_that(workflow_text).does_not_contain("pull_request.head.ref")


def test_workflow_reviews_pr_via_gh_not_working_tree() -> None:
    """The review targets the PR by number, fetching its diff via ``gh``.

    ``lintro review --pr`` collects the PR diff through the GitHub API, so the
    PR's changes are reviewed as data even though the checked-out tree is the
    base ref.
    """
    review_script = SHELL_SCRIPT.read_text(encoding="utf-8")

    assert_that(review_script).contains("lintro review --pr")


@pytest.mark.parametrize(
    "action_ref",
    [
        "step-security/harden-runner@9af89fc71515a100421586dfdb3dc9c984fbf411",
        "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10",
        "astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78",
    ],
)
def test_workflow_pins_actions_to_sha(*, action_ref: str) -> None:
    """Third-party actions are pinned to full commit SHAs.

    Args:
        action_ref: The ``owner/repo@sha`` reference expected in the workflow.
    """
    content = WORKFLOW.read_text(encoding="utf-8")

    assert_that(content).contains(action_ref)
