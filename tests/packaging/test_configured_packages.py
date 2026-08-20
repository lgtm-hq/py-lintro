"""Tests for setuptools-backed package discovery."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from tests.packaging.configured_packages import configured_packages

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_configured_packages_includes_lintro_and_excludes_tests() -> None:
    """The finder must match ``[tool.setuptools.packages.find]``.

    Setuptools, not a hand-rolled walker, decides what ships. The include
    pattern is ``lintro*`` and tests are excluded, so a drift here would
    mean CI import verification and the wheel build disagree.
    """
    packages = configured_packages(project_root=PROJECT_ROOT)

    assert_that(packages).contains("lintro")
    assert_that(packages).contains("lintro.parsers")
    assert_that(packages).contains("lintro.tools.definitions")
    assert_that([name for name in packages if name.startswith("tests")]).is_empty()


def test_configured_packages_main_prints_one_name_per_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CI helper prints package names for import verification.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    from tests.packaging.configured_packages import main

    main()
    names = capsys.readouterr().out.splitlines()
    assert_that(names).contains("lintro")
    assert_that(names).contains("lintro.parsers")
