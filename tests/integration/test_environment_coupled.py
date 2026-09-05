"""Integration tests for behaviour that reads the real host environment.

These started life in ``tests/unit`` but their outcome depends on what the
machine happens to have installed — a Homebrew symlink under
``/usr/local/bin``, a bash that can run the installer, a lintro run over a
real directory. That made the unit suite non-deterministic on a developer
laptop (#2315), so they live here instead (#465).

They run both on the hosted matrix and inside the tools image; nothing here
needs a wrapped tool. Any assertion added later that does need one must gate
on ``tests.integration._tools.require_tool``.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - fixed installer argv; shell=False
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.badge import badge_command
from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core.update_channels import detect_update_channel
from tests.integration._tools import tool_runs_for_lintro

# pytest does not chdir to the repository root, so the installer script
# and its cwd must be resolved from this file rather than Path.cwd().
_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_detect_update_channel_usr_local_bin_is_standalone() -> None:
    """``/usr/local/bin`` is standalone unless the link resolves into Cellar."""
    channel = detect_update_channel("/usr/local/bin/hadolint")
    assert_that(channel).is_equal_to(UpdateChannel.STANDALONE)


def test_installer_dry_run_simulates_spectral_verification() -> None:
    """Dry-run output must not report an uninstalled Spectral binary as missing."""
    environment = os.environ.copy()
    environment["PATH"] = "/usr/bin:/bin"

    result = subprocess.run(  # nosec B603 - fixed repository script and argv
        [
            "/bin/bash",
            str(_REPO_ROOT / "scripts" / "utils" / "install-tools.sh"),
            "--dry-run",
            "--tools",
            "spectral",
        ],
        cwd=_REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("[DRY-RUN] Would verify spectral is available")
    assert_that(result.stdout).does_not_contain("spectral: not found")


@pytest.mark.xfail(
    tool_runs_for_lintro("osv-scanner"),
    strict=True,
    reason=(
        "osv_scanner bypasses lintro's file-discovery pipeline (it finds "
        "lockfiles itself), so over an empty directory it returns "
        "skipped=False with empty output and no 'no files found to check' "
        "marker. badge.py's _result_checked_any_files reads that text "
        "heuristically, so that one result makes an empty scan look like a "
        "real 100/100 run. Product gap, not a test defect: the badge command "
        "publishes a perfect score for a directory nothing inspected."
    ),
)
def test_badge_empty_directory_does_not_publish(tmp_path: Path) -> None:
    """A real empty directory must not publish a 100/100 badge.

    Unmocked companion to the two mocked cases in
    ``tests/unit/cli_utils/commands/test_badge_command.py``: those pin the
    contract at ``resolve_health_score``, this one drives the whole pipeline
    over a directory with nothing in it.

    The xfail above is conditional and ``strict``: it fires only where
    lintro would actually run osv-scanner (present *and* clearing its
    version floor, since below that the plugin skips it and the gap does
    not arise). Everywhere else this must pass outright, and the day the
    gap is closed the XPASS fails the suite so the marker has to be
    removed rather than rotting.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    runner = CliRunner()

    result = runner.invoke(badge_command, [str(tmp_path)])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).does_not_contain("img.shields.io")
