"""Tests for setuptools-backed package discovery."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - subprocess runs uv with a fixed argv, shell=False
import tomllib
from pathlib import Path

import pytest
from assertpy import assert_that

from tests.packaging.configured_packages import configured_packages

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_system_setuptools_pin() -> str:
    """Return the ``setuptools==…`` pin from ``[build-system] requires``.

    Returns:
        The pinned requirement string used by the wheel build.
    """
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        requires = tomllib.load(handle)["build-system"]["requires"]
    pin = next(
        (
            requirement
            for requirement in requires
            if requirement.startswith("setuptools==")
        ),
        "",
    )
    assert_that(pin).is_not_empty()
    return pin


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


def test_verify_imports_script_discovers_packages_without_project_venv() -> None:
    """CI import verification must not require a synced project environment.

    The built-package workflow sets ``BOOTSTRAP_SKIP_SYNC=1``, so
    ``uv run python`` against the repo would fail. Discovery has to use
    ``uv run --no-project --with setuptools==…``.
    """
    script_path = PROJECT_ROOT / "scripts" / "ci" / "test-verify-imports.sh"
    script = script_path.read_text(encoding="utf-8")
    assert_that(script).contains("uv run --no-project --with")
    assert_that(script).contains("BOOTSTRAP_SKIP_SYNC")
    assert_that(script).contains("setuptools==[0-9.]+")
    assert_that(script).does_not_contain("uv run python tests/packaging")


def test_package_discovery_runs_without_project_venv() -> None:
    """The CI discovery command must succeed without a project ``.venv``.

    Mirrors ``scripts/ci/test-verify-imports.sh``: ``uv run --no-project``
    plus the ``[build-system]`` setuptools pin, with ``VIRTUAL_ENV`` cleared.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv is required to run isolated package discovery")

    pin = _build_system_setuptools_pin()
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("UV_PROJECT", None)
    result = subprocess.run(  # nosec B603 B607 - fixed argv resolved from PATH, shell=False
        [
            "uv",
            "run",
            "--no-project",
            "--with",
            pin,
            "python",
            "tests/packaging/configured_packages.py",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert_that(result.returncode).described_as(
        f"isolated discovery failed\nstdout: {result.stdout}\nstderr: {result.stderr}",
    ).is_equal_to(0)
    names = result.stdout.splitlines()
    assert_that(names).contains("lintro")
    assert_that(names).contains("lintro.parsers")
    assert_that([name for name in names if name.startswith("tests")]).is_empty()
