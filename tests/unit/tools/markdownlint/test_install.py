"""Regression coverage for refreshing markdownlint in the application image."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess  # nosec B404 - fixed installer argv in dry-run mode; shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_app_image_refreshes_markdownlint_from_its_own_version_source() -> None:
    """The app build must replace the older binary inherited from its tools pin.

    Exercise the real installer's dry run with the Dockerfile's actual tool
    filter: copying a newer package.json and rendering the manifest alone
    leaves the globally installed markdownlint-cli2 unchanged (#3100).
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("install-tools.sh requires bash >= 4")
    probe = subprocess.run(  # nosec B603 - fixed bash capability probe
        [bash, "-c", "(( BASH_VERSINFO[0] >= 4 ))"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip("install-tools.sh requires bash >= 4")

    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    full_stage = dockerfile.split("FROM tools AS full\n", 1)[1].split("\nFROM ", 1)[0]
    filters = re.findall(
        r"/app/scripts/utils/install-tools\.sh (--docker --tools [\w,-]+)",
        full_stage.replace("\\\n", ""),
    )
    assert_that(filters).described_as("full-stage tool refresh").is_length(1)

    result = (
        subprocess.run(  # nosec B603 - repository installer; dry-run forbids installs
            [
                bash,
                str(_REPO_ROOT / "scripts/utils/install-tools.sh"),
                *shlex.split(filters[0]),
                "--dry-run",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
            cwd=_REPO_ROOT,
        )
    )

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    package = json.loads((_REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    version = package["devDependencies"]["markdownlint-cli2"]
    assert_that(result.stdout).contains(
        f"Would install markdownlint-cli2@{version} globally via bun",
        "Would verify markdownlint-cli2 is available",
    )
