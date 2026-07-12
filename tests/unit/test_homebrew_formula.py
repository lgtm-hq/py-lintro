"""Contract tests for the Homebrew formulas and their CI validation wiring.

These guard the hardening added for issues #820 (brew audit/style CI) and #821
(conflicts_with, richer tests, livecheck, head spec) so the quality bar cannot
silently regress. They assert on file contents rather than invoking ``brew`` so
they run on any platform; the live ``brew audit``/``brew style`` checks run in
the ``CI - Homebrew Formula Lint`` workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOMEBREW_DIR = _REPO_ROOT / "scripts" / "ci" / "homebrew"
_TEMPLATE = _HOMEBREW_DIR / "templates" / "lintro.rb.template"
_BINARY_GENERATOR = _HOMEBREW_DIR / "generate-binary-formula.sh"
_AUDIT_SCRIPT = _HOMEBREW_DIR / "audit-formulas.sh"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "homebrew-formula-lint.yml"


def _load_workflow() -> dict[str, Any]:
    """Parse the Homebrew formula lint workflow.

    Returns:
        The parsed workflow document as a mapping.
    """
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert_that(data).is_instance_of(dict)
    return cast(dict[str, Any], data)


def test_pypi_template_declares_conflicts_head_and_livecheck() -> None:
    """The PyPI formula template carries the #821 hardening stanzas."""
    template = _TEMPLATE.read_text(encoding="utf-8")

    assert_that(template).contains(
        'conflicts_with "lintro", because: "both provide the lintro binary"',
    )
    assert_that(template).contains(
        'head "https://github.com/lgtm-hq/py-lintro.git", branch: "main"',
    )
    assert_that(template).contains("livecheck do")
    assert_that(template).contains("strategy :pypi")


def test_pypi_template_has_richer_test_block() -> None:
    """The template test block verifies version, help and doctor output."""
    template = _TEMPLATE.read_text(encoding="utf-8")

    assert_that(template).contains(
        'assert_match version.to_s, shell_output("#{bin}/lintro --version")',
    )
    assert_that(template).contains(
        'assert_match "Usage:", shell_output("#{bin}/lintro --help")',
    )
    assert_that(template).contains(
        'assert_match "Lintro Doctor", shell_output("#{bin}/lintro doctor", 1)',
    )


def test_pypi_template_has_no_escaped_interpolation() -> None:
    r"""The formula must use real Ruby interpolation, not a literal ``\#{``.

    An escaped ``\#{bin}`` renders to the literal string ``#{bin}`` and breaks
    the test block, so guard against the regression.
    """
    template = _TEMPLATE.read_text(encoding="utf-8")

    assert_that(template).does_not_contain("\\#{bin}")


def test_binary_generator_declares_conflicts_and_livecheck() -> None:
    """The binary formula generator emits the #821 hardening stanzas."""
    generator = _BINARY_GENERATOR.read_text(encoding="utf-8")

    assert_that(generator).contains(
        'conflicts_with "lintro-full", because: "both provide the lintro binary"',
    )
    assert_that(generator).contains("livecheck do")
    assert_that(generator).contains("strategy :github_latest")


def test_binary_generator_uses_strict_sorbet_sigil() -> None:
    """The binary formula must use a strict Sorbet sigil to pass brew style."""
    generator = _BINARY_GENERATOR.read_text(encoding="utf-8")

    assert_that(generator).contains("# typed: strict")
    assert_that(generator).does_not_contain("# typed: false")


def test_binary_generator_has_richer_test_block() -> None:
    """The binary formula test block verifies version, help and doctor output."""
    generator = _BINARY_GENERATOR.read_text(encoding="utf-8")

    assert_that(generator).contains(
        'assert_match "Usage:", shell_output("#{bin}/lintro --help")',
    )
    assert_that(generator).contains(
        'assert_match "Lintro Doctor", shell_output("#{bin}/lintro doctor", 1)',
    )


def test_audit_script_is_executable_and_runs_both_linters() -> None:
    """The audit helper exists, is executable and runs style plus audit."""
    import os

    assert_that(_AUDIT_SCRIPT.exists()).is_true()
    assert_that(os.access(_AUDIT_SCRIPT, os.X_OK)).is_true()

    script = _AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert_that(script).contains("brew style")
    assert_that(script).contains("brew audit --strict")
    # It must validate both formulas.
    assert_that(script).contains("render_formula.py")
    assert_that(script).contains("generate-binary-formula.sh")


def test_workflow_runs_on_macos_with_least_privilege() -> None:
    """The lint workflow runs on macOS with read-only, scoped permissions."""
    workflow = _load_workflow()

    assert_that(workflow["permissions"]).is_equal_to({})
    job = workflow["jobs"]["homebrew-lint"]
    assert_that(job["runs-on"]).is_equal_to("macos-latest")
    assert_that(job["permissions"]).is_equal_to({"contents": "read"})


def test_workflow_hardens_runner_and_pins_actions() -> None:
    """The workflow hardens egress and pins every action to a SHA."""
    workflow = _load_workflow()
    steps = workflow["jobs"]["homebrew-lint"]["steps"]
    uses = [step["uses"] for step in steps if "uses" in step]

    # Every third-party action is pinned to a 40-char commit SHA.
    for action in uses:
        ref = action.split("@", 1)[1]
        assert_that(ref).matches(r"^[0-9a-f]{40}$")

    harden = next(s for s in steps if s.get("uses", "").startswith("step-security/"))
    endpoints = harden["with"]["allowed-endpoints"]
    assert_that(harden["with"]["egress-policy"]).is_equal_to("block")
    # brew needs GitHub plus the Homebrew formulae API.
    assert_that(endpoints).contains("github.com:443")
    assert_that(endpoints).contains("api.github.com:443")
    assert_that(endpoints).contains("formulae.brew.sh:443")


def test_workflow_delegates_to_dedicated_script() -> None:
    """The workflow must call the dedicated script, not inline shell logic."""
    workflow = _load_workflow()
    steps = workflow["jobs"]["homebrew-lint"]["steps"]

    run_steps = [step["run"] for step in steps if "run" in step]
    assert_that(run_steps).is_length(1)
    assert_that(run_steps[0].strip()).is_equal_to(
        "scripts/ci/homebrew/audit-formulas.sh",
    )


def test_workflow_triggers_on_homebrew_paths() -> None:
    """The workflow only runs when Homebrew scripts or the workflow change."""
    workflow = _load_workflow()
    # ``on`` is parsed as the boolean-like key ``True`` by PyYAML.
    on = workflow["on"] if "on" in workflow else workflow[True]
    pr_paths = on["pull_request"]["paths"]

    assert_that(pr_paths).contains("scripts/ci/homebrew/**")
