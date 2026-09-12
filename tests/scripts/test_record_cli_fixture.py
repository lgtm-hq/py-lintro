"""Tests for the agent-CLI replay fixture recorder's help output (#2600).

The recorder itself spends quota and needs the CLIs installed, so what is
testable for free is the part an operator meets first: ``--help``. It is
printed by slicing the script's own header, and a slice that runs past the
header spills the script body at whoever asked for usage.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - runs the repo's own script with a fixed argv
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "record_cli_fixture.sh"

#: First and last lines of the header block the script documents itself with.
_FIRST_LINE = "# Re-record an agent-CLI replay fixture (#2600)."
_LAST_LINE = "# Recording spends quota: it makes one real call."


def _usage(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the recorder and return the completed process.

    Args:
        *args: Arguments to pass to the script.

    Returns:
        The completed process, with output captured as text.
    """
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        [bash, str(_SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("args", "code"),
    [(("--help",), 0), (("-h",), 0), ((), 2)],
)
def test_help_prints_the_header_block_and_nothing_else(
    args: tuple[str, ...],
    code: int,
) -> None:
    """Usage must be the header, whole and alone.

    The block is sliced out of the script by anchors on its first and last
    comment lines. An anchor that does not match runs the slice to
    end-of-file, which answers ``--help`` with the script's body — and that
    still exits 0, so only reading the output catches it.

    Args:
        args: Arguments that must produce usage.
        code: Exit status those arguments must produce.
    """
    result = _usage(*args)

    assert_that(result.returncode).is_equal_to(code)
    lines = result.stdout.splitlines()
    assert_that(lines).is_not_empty()
    assert_that(lines[0]).is_equal_to(_FIRST_LINE)
    assert_that(lines[-1]).is_equal_to(_LAST_LINE)
    # Every line of the block is a comment: the moment the slice overruns the
    # header it picks up `set -euo pipefail` and everything after it.
    non_comments = [line for line in lines if line and not line.startswith("#")]
    assert_that(non_comments).described_as("usage must print comments only").is_empty()
    assert_that(result.stdout).contains("scripts/ci/record_cli_fixture.sh")
