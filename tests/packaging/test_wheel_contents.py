"""Guard tests for the built lintro wheel's contents.

These tests build the wheel with ``uv build`` and inspect its contents
directly to catch packaging regressions such as:

- A subpackage under ``lintro/`` missing from the wheel because it was
  not listed in ``[tool.setuptools.packages.find]``/``exclude``.
- The ``lintro/py.typed`` PEP 561 marker missing from the wheel because
  it was not declared in ``[tool.setuptools.package-data]``.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess builds the wheel under test with fixed argv, shell=False
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _skip_if_uv_missing() -> None:
    """Skip the caller when ``uv`` is not on ``PATH``."""
    if shutil.which("uv") is None:
        pytest.skip("uv is required to build the wheel")


@pytest.fixture(scope="module")
def built_dist_dir() -> Iterator[Path]:
    """Build the lintro wheel and sdist once and yield the dist directory.

    Yields:
        Path: Temporary directory containing the built artifacts.
    """
    _skip_if_uv_missing()
    with tempfile.TemporaryDirectory() as tmpdir:
        dist_dir = Path(tmpdir) / "dist"
        build_result = subprocess.run(  # nosec B603 B607 - fixed argv resolved from PATH, shell=False
            ["uv", "build", "--out-dir", str(dist_dir)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert_that(build_result.returncode).described_as(
            f"uv build failed\nstdout: {build_result.stdout}\n"
            f"stderr: {build_result.stderr}",
        ).is_equal_to(0)
        yield dist_dir


@pytest.fixture(scope="module")
def built_wheel_path(built_dist_dir: Path) -> Path:
    """Return the built wheel path from the shared dist directory.

    Args:
        built_dist_dir: Directory produced by ``uv build``.

    Returns:
        Path: The built wheel file.
    """
    wheels = list(built_dist_dir.glob("*.whl"))
    assert_that(wheels).is_not_empty()
    return wheels[0]


@pytest.fixture(scope="module")
def sdist_namelist(built_dist_dir: Path) -> list[str]:
    """Return archive member names from the built sdist.

    Args:
        built_dist_dir: Directory produced by ``uv build``.

    Returns:
        List of tar member names in the sdist.
    """
    sdists = list(built_dist_dir.glob("*.tar.gz"))
    assert_that(sdists).is_not_empty()
    with tarfile.open(sdists[0], mode="r:gz") as archive:
        return archive.getnames()


def test_skip_if_uv_missing_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wheel fixture must skip, not error, when ``uv`` is absent.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(pytest.skip.Exception, match="uv is required"):
        _skip_if_uv_missing()


@pytest.fixture(scope="module")
def wheel_namelist(built_wheel_path: Path) -> list[str]:
    """Return the list of file names contained in the built wheel.

    Args:
        built_wheel_path: Path to the built wheel file.

    Returns:
        List of archive member names in the wheel.
    """
    with zipfile.ZipFile(built_wheel_path) as archive:
        return archive.namelist()


@pytest.mark.slow
@pytest.mark.packaging
def test_wheel_contains_all_subpackages(wheel_namelist: list[str]) -> None:
    """Every lintro subpackage on disk must be present in the wheel."""
    expected_init_files = {
        str(init_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for init_file in (PROJECT_ROOT / "lintro").rglob("__init__.py")
    }

    missing = sorted(expected_init_files - set(wheel_namelist))

    assert_that(missing).described_as(
        f"Subpackages missing from wheel: {missing}",
    ).is_empty()


@pytest.mark.slow
@pytest.mark.packaging
def test_wheel_does_not_leak_non_lintro_top_level_dirs(
    wheel_namelist: list[str],
) -> None:
    """Only ``lintro`` and its dist-info should appear at the wheel root."""
    top_level_entries = {name.split("/", 1)[0] for name in wheel_namelist}

    unexpected = sorted(
        entry
        for entry in top_level_entries
        if entry != "lintro" and not entry.endswith(".dist-info")
    )

    assert_that(unexpected).described_as(
        f"Unexpected top-level entries leaked into wheel: {unexpected}",
    ).is_empty()


@pytest.mark.slow
@pytest.mark.packaging
def test_py_typed_marker_included_in_wheel(wheel_namelist: list[str]) -> None:
    """The PEP 561 ``py.typed`` marker must ship inside the wheel."""
    assert_that(wheel_namelist).contains("lintro/py.typed")


def _package_data_globs() -> list[tuple[str, str]]:
    """Return ``(package, pattern)`` rows from ``[tool.setuptools.package-data]``.

    Returns:
        Package name and glob pairs in declaration order.
    """
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        package_data = tomllib.load(handle)["tool"]["setuptools"]["package-data"]
    rows: list[tuple[str, str]] = []
    for package_name, patterns in package_data.items():
        for pattern in patterns:
            rows.append((package_name, pattern))
    return rows


def _declared_package_data_paths() -> set[str]:
    """Return on-disk paths selected by ``[tool.setuptools.package-data]``.

    Globs are read from pyproject.toml so a packaging-table change is the
    single source of truth for both the wheel build and this guard.

    Returns:
        Repository-relative POSIX paths for declared package data.
    """
    paths: set[str] = set()
    empty: list[str] = []
    for package_name, pattern in _package_data_globs():
        package_dir = PROJECT_ROOT.joinpath(*package_name.split("."))
        matched = [
            str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
            for path in package_dir.glob(pattern)
            if path.is_file()
        ]
        if not matched:
            empty.append(f"{package_name}: {pattern}")
        paths.update(matched)
    assert_that(empty).described_as(
        f"Package-data globs matched no files: {empty}",
    ).is_empty()
    return paths


@pytest.mark.packaging
def test_declared_package_data_exists_on_disk() -> None:
    """Every package-data glob must match at least one file on disk."""
    paths = _declared_package_data_paths()
    assert_that(paths).contains("lintro/py.typed")
    assert_that(paths).contains("lintro/tools/manifest.json")
    assert_that(_package_data_globs()).is_not_empty()


@pytest.mark.slow
@pytest.mark.packaging
def test_wheel_contains_declared_package_data(wheel_namelist: list[str]) -> None:
    """JSON, prompt templates, and checklist YAML must ship in the wheel."""
    expected = _declared_package_data_paths()
    missing = sorted(expected - set(wheel_namelist))
    assert_that(missing).described_as(
        f"Package-data files missing from wheel: {missing}",
    ).is_empty()


@pytest.mark.slow
@pytest.mark.packaging
def test_sdist_contains_declared_package_data(sdist_namelist: list[str]) -> None:
    """Declared package-data files, including ``py.typed``, must ship in the sdist."""
    members = [name.replace("\\", "/") for name in sdist_namelist]
    missing = [
        path
        for path in sorted(_declared_package_data_paths())
        if not any(member.endswith(path) for member in members)
    ]
    assert_that(missing).described_as(
        f"Package-data files missing from sdist: {missing}",
    ).is_empty()
    assert_that(
        any(member.endswith("lintro/py.typed") for member in members),
    ).is_true()


@pytest.mark.packaging
def test_py_typed_marker_exists_on_disk() -> None:
    """The ``lintro/py.typed`` marker file must exist in the source tree."""
    assert_that((PROJECT_ROOT / "lintro" / "py.typed").exists()).is_true()
