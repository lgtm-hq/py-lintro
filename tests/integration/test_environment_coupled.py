"""Integration tests for behaviour that reads the real host environment.

These started life in ``tests/unit`` but their outcome depends on what the
machine happens to have installed — a Homebrew symlink under
``/usr/local/bin``, a bash that can run the installer, a lintro run over a
real directory. That made the unit suite non-deterministic on a developer
laptop (#2315), so they live here instead, where the tools image gives them a
fixed environment (#465).
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - fixed installer argv; shell=False
from pathlib import Path

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.badge import badge_command
from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core.update_channels import detect_update_channel


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
            "scripts/utils/install-tools.sh",
            "--dry-run",
            "--tools",
            "spectral",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("[DRY-RUN] Would verify spectral is available")
    assert_that(result.stdout).does_not_contain("spectral: not found")


def test_badge_empty_directory_does_not_publish(tmp_path: Path) -> None:
    """A real empty directory must not publish a 100/100 badge.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    runner = CliRunner()

    result = runner.invoke(badge_command, [str(tmp_path)])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).does_not_contain("img.shields.io")
