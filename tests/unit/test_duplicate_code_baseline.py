"""Ratchet gate for the duplicate-code baseline (issue #2293).

``pyproject.toml`` scopes pylint's ``duplicate-code`` checker to
``lintro/tools/definitions`` and records today's ``R0801`` count as
``[tool.lintro.pylint] duplicate_code_baseline``. That number is a burn-down
target owned by #2311, which is done when it reaches 0.

Two guards live here. The configured baseline may never exceed the ceiling
recorded when the gate landed, and — wherever pylint is installed, which
includes CI — it must equal what pylint actually reports on the definitions
package. Together they stop a baseline drifting above the truth (hiding new
duplication) or below it (failing every run): a raise is only possible by
raising the ceiling too, which is what review looks for.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - pylint is invoked with a fixed argument list
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
from assertpy import assert_that

from lintro.utils.duplicate_code import (
    DUPLICATE_CODE_BASELINE_KEY,
    DUPLICATE_CODE_MESSAGE_ID,
    resolve_duplicate_code_baseline,
)

#: Repository root, resolved from this file's location.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

#: The package the gate is scoped to.
DEFINITIONS_PACKAGE: str = "lintro/tools/definitions"

#: Ceiling on the baseline, recorded when the gate landed and deliberately not
#: derived from the config. It may only go *down*: lower it in the pull request
#: that removes duplication, never raise it. #2311 drives it to 0.
MAX_ALLOWED_DUPLICATE_CODE_BASELINE: int = 34

#: Minimum clone length pylint counts, from ``[tool.pylint.similarities]``.
#: A ratchet that quietly raised this would make the count fall without any
#: duplication being removed.
MAX_ALLOWED_MIN_SIMILARITY_LINES: int = 12


def _load_pyproject() -> dict[str, Any]:
    """Load the repository ``pyproject.toml``.

    Returns:
        dict[str, Any]: The parsed document.
    """
    path = REPO_ROOT / "pyproject.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _lintro_pylint_config() -> dict[str, Any]:
    """Return the ``[tool.lintro.pylint]`` table.

    Returns:
        dict[str, Any]: The gate's configuration table.
    """
    parsed = _load_pyproject()
    return cast("dict[str, Any]", parsed["tool"]["lintro"]["pylint"])


def _measure_duplicate_code_count(*, pylint_executable: str) -> int:
    """Run pylint over the definitions package and count ``R0801`` messages.

    Args:
        pylint_executable: Absolute path to the pylint binary.

    Returns:
        int: Number of duplicate-code findings pylint reports.
    """
    # rglob, not glob: the production include filter matches by path prefix,
    # so a future subpackage would be analysed there and must be counted here.
    files = sorted(
        str(path)
        for path in (REPO_ROOT / DEFINITIONS_PACKAGE).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    completed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [
            pylint_executable,
            "--output-format=json2",
            "--rcfile",
            str(REPO_ROOT / "pyproject.toml"),
            *files,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    report = json.loads(completed.stdout)
    messages = report.get("messages", [])
    return sum(
        1
        for message in messages
        if message.get("messageId") == DUPLICATE_CODE_MESSAGE_ID
    )


def test_pylint_is_scoped_to_the_definitions_package() -> None:
    """The gate stays scoped to the package #2311 deduplicates."""
    assert_that(_lintro_pylint_config()["include"]).is_equal_to(
        [DEFINITIONS_PACKAGE],
    )


def test_baseline_is_an_integer_within_its_ceiling() -> None:
    """The configured baseline is an int that never rises above the ceiling."""
    config = _lintro_pylint_config()

    assert_that(config[DUPLICATE_CODE_BASELINE_KEY]).is_instance_of(int)
    baseline = resolve_duplicate_code_baseline(config=config)
    assert_that(baseline).is_not_none()
    assert_that(baseline).is_less_than_or_equal_to(
        MAX_ALLOWED_DUPLICATE_CODE_BASELINE,
    )


def test_min_similarity_lines_is_not_relaxed() -> None:
    """The clone length pylint counts may be lowered, never raised."""
    similarities = _load_pyproject()["tool"]["pylint"]["similarities"]

    assert_that(similarities["min-similarity-lines"]).is_less_than_or_equal_to(
        MAX_ALLOWED_MIN_SIMILARITY_LINES,
    )


@pytest.mark.skipif(
    shutil.which("pylint") is None,
    reason="pylint is not installed in this environment",
)
def test_baseline_matches_what_pylint_reports() -> None:
    """The recorded baseline is the count pylint reports on the fixture set."""
    executable = shutil.which("pylint")
    assert executable is not None  # narrow type for mypy; guarded by skipif
    baseline = resolve_duplicate_code_baseline(config=_lintro_pylint_config())

    count = _measure_duplicate_code_count(pylint_executable=executable)

    assert_that(count).is_equal_to(baseline)
