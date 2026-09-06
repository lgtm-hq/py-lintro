"""Ratchet gate for the duplicate-code baseline (issue #2293).

``pyproject.toml`` scopes pylint's ``duplicate-code`` checker to the
tool-definition modules — ``lintro/tools/definitions`` plus the per-tool
packages #2311 has moved definitions into — and records today's ``R0801``
count as
``[tool.lintro.pylint] duplicate_code_baseline``. That number is a burn-down
target owned by #2311, which is done when it reaches 0.

Two guards live here. The configured baseline may never exceed the ceiling
recorded when the gate landed — an exact, tool-free comparison between
``pyproject.toml`` and this module's constant — and, wherever pylint is
installed (which includes CI), what pylint actually reports on those packages
must not be *above* the baseline.

The live comparison is deliberately ``<=`` rather than ``==``. pylint's
``R0801`` count is a property of the resolved toolchain as well as the code:
the same tree reported 34 clone sets on one CI interpreter and 33 on another
(#2365), so an equality assertion turns routine environment drift into a red
required check. ``<=`` keeps the guard's purpose — a count that grows above the
baseline still fails, so the baseline can never drift above the truth and hide
new duplication — while a live count *below* the baseline is exactly what the
ratchet is for: the prompt to lower the recorded number once the drop is
reproducible rather than environment-dependent.
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

#: The packages the gate is scoped to, mirroring ``[tool.lintro.pylint]
#: include``. #2311 moves each tool into its own ``lintro/tools/<name>``
#: package, and the gate's scope follows the modules it moves so a definition
#: cannot escape the ratchet by changing address.
GATE_PACKAGES: tuple[str, ...] = (
    "lintro/tools/actionlint",
    "lintro/tools/astro_check",
    "lintro/tools/bandit",
    "lintro/tools/black",
    "lintro/tools/buf",
    "lintro/tools/cargo_audit",
    "lintro/tools/cargo_deny",
    "lintro/tools/clippy",
    "lintro/tools/commitlint",
    "lintro/tools/definitions",
    "lintro/tools/dotenv_linter",
    "lintro/tools/gitleaks",
    "lintro/tools/golangci_lint",
    "lintro/tools/hadolint",
    "lintro/tools/html_validate",
    "lintro/tools/idiom_review",
    "lintro/tools/import_linter",
    "lintro/tools/markdownlint",
    "lintro/tools/mypy",
    "lintro/tools/osv_scanner",
    "lintro/tools/oxfmt",
    "lintro/tools/oxlint",
    "lintro/tools/pip_audit",
    "lintro/tools/prettier",
    "lintro/tools/pytest",
    "lintro/tools/ruff",
)

#: Ceiling on the baseline, recorded when the gate landed and deliberately not
#: derived from the config. It may only go *down*: lower it in the pull request
#: that removes duplication, never raise it. #2311 drove it to 0, so the gate is
#: zero-tolerance and this constant has nowhere left to fall. The configured
#: baseline is asserted to be ``<=`` this ceiling; no tool runs, so no
#: environment can move it.
MAX_ALLOWED_DUPLICATE_CODE_BASELINE: int = 0

#: pylint exit-status bit meaning "a fatal message was issued".
PYLINT_FATAL_EXIT_BIT: int = 1

#: pylint exit-status bit meaning "usage error" (bad arguments or rcfile).
PYLINT_USAGE_EXIT_BIT: int = 32

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
    """Run pylint over the gate's packages and count ``R0801`` messages.

    Args:
        pylint_executable: Absolute path to the pylint binary.

    Returns:
        int: Number of duplicate-code findings pylint reports.
    """
    # rglob, not glob: the production include filter matches by path prefix,
    # so a future subpackage would be analysed there and must be counted here.
    files = sorted(
        str(path)
        for package in GATE_PACKAGES
        for path in (REPO_ROOT / package).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    # A ``<=`` comparison passes on a count of zero, so a measurement that
    # analysed nothing would look like a clean sweep. Fail loudly instead.
    assert_that(files).described_as(
        f"no modules found under {', '.join(GATE_PACKAGES)} to analyse",
    ).is_not_empty()
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
    # pylint's exit status is a bitmask; bit 1 is "fatal message issued" and
    # bit 32 "usage error". Either means the run never measured the package.
    fatal_bits = completed.returncode & (PYLINT_FATAL_EXIT_BIT | PYLINT_USAGE_EXIT_BIT)
    assert_that(fatal_bits).described_as(
        f"pylint failed to run (exit {completed.returncode}): {completed.stderr}",
    ).is_zero()
    report = json.loads(completed.stdout)
    messages = report.get("messages", [])
    return sum(
        1
        for message in messages
        if message.get("messageId") == DUPLICATE_CODE_MESSAGE_ID
    )


def test_pylint_is_scoped_to_the_definition_packages() -> None:
    """The gate stays scoped to the modules #2311 deduplicates."""
    assert_that(_lintro_pylint_config()["include"]).is_equal_to(
        list(GATE_PACKAGES),
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


def _over_baseline_message(*, count: int, baseline: int) -> str:
    """Render the explanation shown when a live pylint count breaks the ratchet.

    Args:
        count: Number of ``R0801`` findings the live pylint run reported.
        baseline: Baseline recorded in ``pyproject.toml``.

    Returns:
        str: The assertion description, spelling out the ratchet direction and
        what to do when the live count is lower than the baseline instead.
    """
    return (
        f"pylint reports {count} duplicate-code findings on "
        f"{', '.join(GATE_PACKAGES)} "
        f"but the recorded baseline is {baseline}; the baseline may only shrink, "
        "so remove the new duplication rather than raising the number (#2293). "
        "A live count *below* the baseline never fails here — it is the prompt "
        f"to lower {DUPLICATE_CODE_BASELINE_KEY} in pyproject.toml and "
        "MAX_ALLOWED_DUPLICATE_CODE_BASELINE in this module, once the drop is "
        "reproducible rather than environment-dependent (#2365)."
    )


def _assert_within_baseline(*, count: int, baseline: int) -> None:
    """Assert a live pylint count has not risen above the baseline.

    Args:
        count: Number of ``R0801`` findings the live pylint run reported.
        baseline: Baseline recorded in ``pyproject.toml``.
    """
    assert_that(count).described_as(
        _over_baseline_message(count=count, baseline=baseline),
    ).is_less_than_or_equal_to(baseline)


def test_a_live_count_at_the_baseline_is_accepted() -> None:
    """The live comparison passes for a count that has not grown.

    The baseline reached 0 in #2311, so a clone-free definitions package is the
    only count the gate now accepts; anything else is growth.
    """
    _assert_within_baseline(
        count=MAX_ALLOWED_DUPLICATE_CODE_BASELINE,
        baseline=MAX_ALLOWED_DUPLICATE_CODE_BASELINE,
    )


def test_a_live_count_above_the_baseline_is_rejected() -> None:
    """The live comparison still fails when the count grows."""
    with pytest.raises(AssertionError) as excinfo:
        _assert_within_baseline(
            count=MAX_ALLOWED_DUPLICATE_CODE_BASELINE + 1,
            baseline=MAX_ALLOWED_DUPLICATE_CODE_BASELINE,
        )

    assert_that(str(excinfo.value)).contains("may only shrink")


def test_the_over_baseline_message_explains_both_directions() -> None:
    """The failure text names the ratchet rule and the lower-count remedy."""
    message = _over_baseline_message(
        count=MAX_ALLOWED_DUPLICATE_CODE_BASELINE + 1,
        baseline=MAX_ALLOWED_DUPLICATE_CODE_BASELINE,
    )

    assert_that(message).contains(DUPLICATE_CODE_BASELINE_KEY)
    assert_that(message).contains("MAX_ALLOWED_DUPLICATE_CODE_BASELINE")
    assert_that(message).contains("*below* the baseline")


@pytest.mark.skipif(
    shutil.which("pylint") is None,
    reason="pylint is not installed in this environment",
)
def test_pylint_reports_no_more_than_the_baseline() -> None:
    """The live pylint count has not risen above the recorded baseline.

    Not an equality check: the count depends on the resolved pylint/astroid
    build as well as on the code (#2365), and only an *increase* means new
    duplication. A lower count is the signal to lower the baseline.
    """
    executable = shutil.which("pylint")
    assert executable is not None  # narrow type for mypy; guarded by skipif
    baseline = resolve_duplicate_code_baseline(config=_lintro_pylint_config())
    # resolve_duplicate_code_baseline returns None when the gate is unconfigured;
    # test_baseline_is_an_integer_within_its_ceiling covers that separately.
    assert baseline is not None  # narrow type for mypy

    count = _measure_duplicate_code_count(pylint_executable=executable)

    _assert_within_baseline(count=count, baseline=baseline)
