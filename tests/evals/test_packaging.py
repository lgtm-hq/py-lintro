"""The eval harness must never ship in the lintro distribution."""

from __future__ import annotations

import tomllib
from pathlib import Path

from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_evals_is_excluded_from_package_discovery() -> None:
    """Package discovery cannot pull an eval module into the wheel.

    The build uses ``[tool.setuptools.packages.find]`` (#1225): discovery is
    limited to ``lintro*`` and ``evals*`` is excluded outright, so neither the
    harness package nor a future ``evals`` subpackage can ship.
    """
    data = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    find = data["tool"]["setuptools"]["packages"]["find"]

    assert_that(find["include"]).is_equal_to(["lintro*"])
    assert_that(find["exclude"]).contains("evals*")


def test_manifest_prunes_the_evals_directory() -> None:
    """The sdist manifest prunes evals/ so the harness never ships.

    The directive is matched as a whole line: ``prune evals-something-else``
    would satisfy a substring check while pruning a different directory.
    """
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    directives = [line.strip() for line in manifest.splitlines()]

    assert_that(directives).contains("prune evals")


def test_mypy_path_points_at_the_harness_root() -> None:
    """Mypy resolves ``review_matrix`` from the harness root, not the package.

    This is one of the three places that spell the harness root; the others
    are tests/evals/conftest.py and evals/review-efficacy/run_matrix.py.
    """
    data = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )

    mypy_path = data["tool"]["mypy"]["mypy_path"]

    assert_that(mypy_path).contains("evals/review-efficacy")
    assert_that((REPO_ROOT / "evals" / "review-efficacy").is_dir()).is_true()


def test_harness_lives_outside_the_lintro_package() -> None:
    """The harness package sits under evals/, not under lintro/."""
    harness = REPO_ROOT / "evals" / "review-efficacy" / "review_matrix"

    assert_that(harness.is_dir()).is_true()
    assert_that((REPO_ROOT / "lintro" / "review_matrix").exists()).is_false()
