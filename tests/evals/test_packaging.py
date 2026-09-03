"""The eval harness must never ship in the lintro distribution."""

from __future__ import annotations

import tomllib
from pathlib import Path

from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_evals_is_not_a_configured_package() -> None:
    """No eval module is listed in the wheel's explicit package list."""
    data = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    packages = data["tool"]["setuptools"]["packages"]

    offenders = [name for name in packages if "eval" in name.split(".")]

    assert_that(offenders).is_empty()
    assert_that(packages).does_not_contain("review_matrix")


def test_manifest_prunes_the_evals_directory() -> None:
    """The sdist manifest prunes evals/ so the harness never ships."""
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert_that(manifest).contains("prune evals")


def test_harness_lives_outside_the_lintro_package() -> None:
    """The harness package sits under evals/, not under lintro/."""
    harness = REPO_ROOT / "evals" / "review-efficacy" / "review_matrix"

    assert_that(harness.is_dir()).is_true()
    assert_that((REPO_ROOT / "lintro" / "review_matrix").exists()).is_false()
