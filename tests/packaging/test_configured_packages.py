"""Tests for setuptools-backed package discovery."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - subprocess runs uv with a fixed argv, shell=False
import tomllib
from pathlib import Path

import pytest
from assertpy import assert_that

from tests.packaging.configured_packages import (
    build_system_setuptools_pin,
    configured_packages,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_system_setuptools_pin() -> str:
    """Return the ``setuptools==…`` pin from ``[build-system] requires``.

    Returns:
        The pinned requirement string used by the wheel build.
    """
    return build_system_setuptools_pin(project_root=PROJECT_ROOT)


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
    assert_that(script).contains("build_system_setuptools_pin")
    assert_that(script).does_not_contain("grep -m1")
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


def test_configured_packages_module_does_not_import_setuptools() -> None:
    """Importing the helper must not require setuptools at collection time."""
    import ast

    source = (
        PROJECT_ROOT / "tests" / "packaging" / "configured_packages.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
            assert_that(names).does_not_contain("setuptools")
        if isinstance(node, ast.ImportFrom) and node.module:
            assert_that(node.module.split(".", 1)[0]).is_not_equal_to("setuptools")


def test_test_extra_and_tox_pin_setuptools() -> None:
    """The test extra and tox env must ship the build-system setuptools pin."""
    pin = _build_system_setuptools_pin()
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        extras = tomllib.load(handle)["project"]["optional-dependencies"]["test"]
    assert_that(extras).contains(pin)
    tox = (PROJECT_ROOT / "tox.ini").read_text(encoding="utf-8")
    assert_that(tox).contains(pin)


def test_build_system_setuptools_pin_requires_build_system_table(
    tmp_path: Path,
) -> None:
    """Pin extraction fails closed when ``[build-system]`` has no setuptools.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["wheel"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="setuptools pin"):
        build_system_setuptools_pin(project_root=tmp_path)


def test_configured_packages_raises_when_setuptools_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery must fail loudly instead of crashing pytest collection.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import builtins

    real_import = builtins.__import__

    def _hide_setuptools(name: str, *args: object, **kwargs: object) -> object:
        if name == "setuptools" or name.startswith("setuptools."):
            raise ImportError("setuptools hidden for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _hide_setuptools)
    with pytest.raises(ModuleNotFoundError, match="setuptools"):
        configured_packages(project_root=PROJECT_ROOT)
