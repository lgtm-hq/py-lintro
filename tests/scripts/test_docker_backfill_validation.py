"""Tests for docker backfill dispatch input validation."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = (_REPO_ROOT / "scripts/ci/validate-docker-backfill-inputs.sh").resolve()


def _run_validation(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the backfill validation script with the given environment."""
    return subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ.copy(), **env},
    )


@pytest.mark.parametrize(
    ("env", "expected_message"),
    [
        (
            {
                "BACKFILL_VERSION": "0.65.0",
                "BACKFILL_REF": "",
                "FORCE_PUBLISH": "false",
            },
            "BACKFILL_REF is required when BACKFILL_VERSION is set",
        ),
        (
            {
                "BACKFILL_VERSION": "",
                "BACKFILL_REF": "v0.65.0",
                "FORCE_PUBLISH": "false",
            },
            "BACKFILL_VERSION is required when BACKFILL_REF is set",
        ),
        (
            {
                "BACKFILL_VERSION": "",
                "BACKFILL_REF": "",
                "FORCE_PUBLISH": "true",
            },
            "FORCE_PUBLISH cannot be true when BACKFILL_VERSION is empty",
        ),
        (
            {
                "BACKFILL_VERSION": " ",
                "BACKFILL_REF": "v0.65.0",
                "FORCE_PUBLISH": "false",
            },
            "BACKFILL_VERSION is required when BACKFILL_REF is set",
        ),
    ],
    ids=[
        "missing-backfill-ref",
        "missing-backfill-version",
        "force-publish-without-version",
        "whitespace-only-backfill-version",
    ],
)
def test_validate_docker_backfill_inputs_rejects_invalid(
    env: dict[str, str],
    expected_message: str,
) -> None:
    """Invalid dispatch input combinations should fail with clear errors."""
    result = _run_validation(env=env)
    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains(expected_message)


@pytest.mark.parametrize(
    "env",
    [
        {
            "BACKFILL_VERSION": "",
            "BACKFILL_REF": "",
            "FORCE_PUBLISH": "false",
        },
        {
            "BACKFILL_VERSION": "0.65.0",
            "BACKFILL_REF": "v0.65.0",
            "FORCE_PUBLISH": "false",
        },
        {
            "BACKFILL_VERSION": "0.65.0",
            "BACKFILL_REF": "v0.65.0",
            "FORCE_PUBLISH": "true",
        },
    ],
    ids=[
        "default-dispatch",
        "backfill-pair",
        "force-publish-with-version",
    ],
)
def test_validate_docker_backfill_inputs_accepts_valid(
    env: dict[str, str],
) -> None:
    """Valid dispatch input combinations should pass validation."""
    result = _run_validation(env=env)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Docker backfill inputs are valid")


def test_validate_docker_backfill_inputs_exposes_help() -> None:
    """The validation script should support --help."""
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
