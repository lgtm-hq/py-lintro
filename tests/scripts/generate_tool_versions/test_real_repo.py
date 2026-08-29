"""End-to-end smoke tests against the real repository sources.

Since #2180 the generated artifacts are no longer committed, so these tests
generate into a temp directory from the real repo's sources (via the shared
``generated_version_artifacts`` fixture) and assert the outputs are
formatter-clean, instead of ``--check``-ing committed copies.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import sys
from pathlib import Path

from assertpy import assert_that

from tests.scripts.generate_tool_versions.conftest import REPO_ROOT, SCRIPT_PATH


def test_generator_runs_clean_against_real_repo_sources(
    generated_version_artifacts: Path,
) -> None:
    """Generation from the real repo sources succeeds and yields both outputs.

    Args:
        generated_version_artifacts: Session dir with the rendered outputs.
    """
    generated = (generated_version_artifacts / "_generated_versions.py").read_text()
    assert_that(generated).contains("NPM_VERSIONS")
    assert_that(generated).contains("PYPI_VERSIONS")

    manifest = json.loads(
        (generated_version_artifacts / "manifest.json").read_text(),
    )
    tools = {t["name"]: t for t in manifest["tools"]}
    assert_that(len(tools)).is_greater_than(30)
    for entry in tools.values():
        assert_that(entry).described_as(entry["name"]).contains_key("version")


def test_generator_cli_is_idempotent_in_working_tree() -> None:
    """Running the CLI twice leaves a synchronized tree (write then check).

    The working tree's artifacts are build products now; the CLI must
    converge in one write so ``just generate`` is deterministic.
    """
    write_rc = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [sys.executable, str(SCRIPT_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(write_rc.returncode).described_as(
        write_rc.stdout + write_rc.stderr,
    ).is_equal_to(0)

    check_rc = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [sys.executable, str(SCRIPT_PATH), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(check_rc.returncode).described_as(
        check_rc.stdout + check_rc.stderr,
    ).is_equal_to(0)


def test_generated_module_passes_black(
    generated_version_artifacts: Path,
) -> None:
    """The generator's output is byte-equivalent to what black would produce.

    Guards against emitter regressions that would make the formatter and the
    generated output fight each other on every build.

    Args:
        generated_version_artifacts: Session dir with the rendered outputs.
    """
    generated_path = generated_version_artifacts / "_generated_versions.py"
    rc = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [sys.executable, "-m", "black", "--check", "--quiet", str(generated_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(rc.returncode).described_as(rc.stdout + rc.stderr).is_equal_to(0)


def test_generated_module_passes_ruff(
    generated_version_artifacts: Path,
) -> None:
    """The generator's output passes ruff without modification.

    Args:
        generated_version_artifacts: Session dir with the rendered outputs.
    """
    generated_path = generated_version_artifacts / "_generated_versions.py"
    rc = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [sys.executable, "-m", "ruff", "check", str(generated_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(rc.returncode).described_as(rc.stdout + rc.stderr).is_equal_to(0)
