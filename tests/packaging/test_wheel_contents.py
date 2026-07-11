"""Guard tests for the built lintro wheel's contents.

These tests build the wheel with ``uv build`` and inspect its contents
directly to catch packaging regressions such as:

- A subpackage under ``lintro/`` missing from the wheel because it was
  not listed in ``[tool.setuptools.packages.find]``/``exclude``.
- The ``lintro/py.typed`` PEP 561 marker missing from the wheel because
  it was not declared in ``[tool.setuptools.package-data]``.
"""

from __future__ import annotations

import subprocess
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def built_wheel_path() -> Iterator[Path]:
    """Build the lintro wheel once and yield its path.

    Yields:
        Path: The built wheel file in a temporary output directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        dist_dir = Path(tmpdir) / "dist"
        build_result = subprocess.run(
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

        wheels = list(dist_dir.glob("*.whl"))
        assert_that(wheels).is_not_empty()
        yield wheels[0]


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


@pytest.mark.packaging
def test_py_typed_marker_exists_on_disk() -> None:
    """The ``lintro/py.typed`` marker file must exist in the source tree."""
    assert_that((PROJECT_ROOT / "lintro" / "py.typed").exists()).is_true()
