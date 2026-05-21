"""Integration test for built package installation.

This module tests that lintro can be installed as a built wheel distribution
and imported successfully, catching circular import issues that only manifest
when the package is installed (not in editable mode).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that


@pytest.mark.slow
def test_built_wheel_imports() -> None:
    """Test that lintro can be built and imported as a wheel.

    This test:
    1. Builds lintro as a wheel
    2. Installs it in a fresh virtual environment
    3. Attempts to import critical modules
    4. Verifies no circular import errors occur

    This catches issues that only manifest when lintro is installed as a
    dependency (built distribution) rather than in editable mode.
    """
    project_root = Path(__file__).parent.parent.parent

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        venv_path = tmpdir_path / "test_venv"
        dist_dir = tmpdir_path / "dist"

        # Step 1: Build the wheel
        build_result = subprocess.run(
            ["uv", "build", "--out-dir", str(dist_dir)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert_that(build_result.returncode).is_equal_to(0)
        assert_that(dist_dir.exists()).is_true()

        # Find the built wheel
        wheels = list(dist_dir.glob("*.whl"))
        assert_that(wheels).is_not_empty()
        wheel_path = wheels[0]

        # Step 2: Create a fresh virtual environment
        venv_result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert_that(venv_result.returncode).is_equal_to(0)

        # Determine the Python executable in the venv
        if sys.platform == "win32":
            python_exe = venv_path / "Scripts" / "python.exe"
        else:
            python_exe = venv_path / "bin" / "python"

        assert_that(python_exe.exists()).is_true()

        # Step 3: Install the wheel in the venv
        install_result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", str(wheel_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert_that(install_result.returncode).is_equal_to(0)

        # Step 4: Test importing lintro modules (this is where circular imports fail)
        test_imports = [
            "import lintro",
            "import lintro.parsers",
            "from lintro.parsers import bandit",
            "from lintro.parsers.actionlint.actionlint_parser import parse_actionlint_output",  # noqa: E501
            "from lintro.plugins import ToolRegistry; ToolRegistry.get('actionlint')",
            "from lintro.cli import cli",
        ]

        for import_statement in test_imports:
            import_result = subprocess.run(
                [str(python_exe), "-c", import_statement],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert_that(import_result.returncode).described_as(
                f"Import failed: {import_statement}\n"
                f"stdout: {import_result.stdout}\n"
                f"stderr: {import_result.stderr}",
            ).is_equal_to(0)

        # Step 5: Test that lintro CLI works
        cli_result = subprocess.run(
            [str(python_exe), "-m", "lintro", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert_that(cli_result.returncode).is_equal_to(0)
        assert_that(cli_result.stdout).contains("lintro")


@pytest.mark.slow
def test_built_wheel_with_full_extra() -> None:
    """Test that lintro[full] extra installs bundled Python tools."""
    project_root = Path(__file__).parent.parent.parent

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        venv_path = tmpdir_path / "test_venv"
        dist_dir = tmpdir_path / "dist"

        build_result = subprocess.run(
            ["uv", "build", "--out-dir", str(dist_dir)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert_that(build_result.returncode).is_equal_to(0)

        wheels = list(dist_dir.glob("*.whl"))
        assert_that(wheels).is_not_empty()
        wheel_path = wheels[0]

        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
            timeout=30,
        )

        if sys.platform == "win32":
            python_exe = venv_path / "Scripts" / "python.exe"
        else:
            python_exe = venv_path / "bin" / "python"

        install_result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", f"{wheel_path}[full]"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert_that(install_result.returncode).is_equal_to(0)

        for module in ("ruff", "black", "mypy", "bandit", "pydoclint", "yamllint"):
            import_result = subprocess.run(
                [str(python_exe), "-c", f"import {module}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert_that(import_result.returncode).described_as(
                f"Expected {module} from lintro[full]",
            ).is_equal_to(0)
