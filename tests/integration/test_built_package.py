"""Integration test for built package installation.

This module tests that lintro can be installed as a built wheel distribution
and imported successfully, catching circular import issues that only manifest
when the package is installed (not in editable mode).
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

import pytest
from assertpy import assert_that


@pytest.mark.slow
def test_built_wheel_imports(built_distributions: Path) -> None:
    """Test that lintro can be installed and imported as a wheel.

    This test:
    1. Installs the session's wheel in a fresh virtual environment
    2. Attempts to import critical modules
    3. Verifies no circular import errors occur

    This catches issues that only manifest when lintro is installed as a
    dependency (built distribution) rather than in editable mode.

    Args:
        built_distributions: Directory holding the session's wheel and sdist.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        venv_path = tmpdir_path / "test_venv"

        wheels = list(built_distributions.glob("*.whl"))
        assert_that(wheels).is_not_empty()
        wheel_path = wheels[0]

        # Step 2: Create a fresh virtual environment
        venv_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
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
        install_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
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
            "from lintro.parsers.actionlint.actionlint_parser import parse_actionlint_output",
            "from lintro.plugins import ToolRegistry; ToolRegistry.get('actionlint')",
            "from lintro.cli import cli",
        ]

        for import_statement in test_imports:
            import_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
                [str(python_exe), "-c", import_statement],
                # Import the installed wheel, not the source tree the test
                # runner happens to sit in.
                cwd=str(tmpdir_path),
                env=_isolated_env(),
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
        cli_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            [str(python_exe), "-m", "lintro", "--version"],
            cwd=str(tmpdir_path),
            env=_isolated_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert_that(cli_result.returncode).is_equal_to(0)
        assert_that(cli_result.stdout).contains("lintro")


@pytest.mark.slow
def test_built_wheel_with_full_extra(built_distributions: Path) -> None:
    """Test that lintro[full] extra installs bundled Python tools.

    Args:
        built_distributions: Directory holding the session's wheel and sdist.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        venv_path = tmpdir_path / "test_venv"

        wheels = list(built_distributions.glob("*.whl"))
        assert_that(wheels).is_not_empty()
        wheel_path = wheels[0]

        subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            [sys.executable, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
            timeout=30,
        )

        if sys.platform == "win32":
            python_exe = venv_path / "Scripts" / "python.exe"
        else:
            python_exe = venv_path / "bin" / "python"

        install_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            [str(python_exe), "-m", "pip", "install", f"{wheel_path}[full]"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert_that(install_result.returncode).is_equal_to(0)

        for module in ("ruff", "black", "mypy", "bandit", "pydoclint", "yamllint"):
            import_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
                [str(python_exe), "-c", f"import {module}"],
                cwd=str(tmpdir_path),
                env=_isolated_env(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert_that(import_result.returncode).described_as(
                f"Expected {module} from lintro[full]",
            ).is_equal_to(0)


def _isolated_env() -> dict[str, str]:
    """Build a subprocess environment that cannot reach the source tree.

    ``cwd`` alone is not enough: an inherited ``PYTHONPATH`` would put the
    checkout back on ``sys.path`` and let a module missing from the built
    distribution satisfy an import. This mirrors ``run_isolated`` in
    ``scripts/ci/test-verify-imports.sh``.

    Returns:
        A copy of the current environment with ``PYTHONPATH`` removed.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _build_distributions(dist_dir: Path) -> None:
    """Build the wheel and the sdist into ``dist_dir``.

    Args:
        dist_dir: Directory the distributions are written to.
    """
    project_root = Path(__file__).parent.parent.parent

    build_result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["uv", "build", "--out-dir", str(dist_dir)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert_that(build_result.returncode).described_as(
        f"uv build failed:\n{build_result.stdout}\n{build_result.stderr}",
    ).is_equal_to(0)


_BUILD_LOCK_TIMEOUT_SECONDS = 900.0
_BUILD_POLL_SECONDS = 0.5


@pytest.fixture(scope="session")
def built_distributions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel and the sdist once for the whole session.

    ``uv build`` keeps its scratch state inside the project directory —
    ``build/``, ``lintro.egg-info/`` and the sdist staging tree — so two builds
    running at once corrupt each other. Under ``pytest -n auto`` that produced
    both outright build failures ("could not delete ...: No such file or
    directory") and, worse, wheels silently missing packages. One build behind
    a lock file, shared by every xdist worker, makes the artifacts
    deterministic and cuts the suite from four builds to one.

    Args:
        tmp_path_factory: Session-scoped temporary directory factory.

    Returns:
        Directory holding the built wheel and source distribution.

    Raises:
        TimeoutError: If another worker holds the build lock for too long.
    """
    # Under xdist each worker gets ``.../pytest-<n>/popen-gw<k>`` and the
    # session root they share is its parent. Without xdist ``getbasetemp()``
    # already is that root, and its own parent persists across sessions, which
    # would hand a later run a stale wheel.
    basetemp = tmp_path_factory.getbasetemp()
    shared_root = basetemp.parent if os.environ.get("PYTEST_XDIST_WORKER") else basetemp
    dist_dir = shared_root / "lintro-dist"
    marker = shared_root / "lintro-dist.complete"
    lock = shared_root / "lintro-dist.lock"

    deadline = time.monotonic() + _BUILD_LOCK_TIMEOUT_SECONDS
    while not marker.exists():
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Timed out waiting for the shared build lock at {lock}",
                ) from exc
            time.sleep(_BUILD_POLL_SECONDS)
            continue
        try:
            dist_dir.mkdir(exist_ok=True)
            _build_distributions(dist_dir=dist_dir)
            marker.touch()
        finally:
            os.close(handle)
            lock.unlink(missing_ok=True)

    return dist_dir


def _source_packages() -> set[str]:
    """Collect every package directory in the lintro source tree.

    Returns:
        Relative POSIX paths of directories holding an ``__init__.py``.
    """
    project_root = Path(__file__).parent.parent.parent

    return {
        init.parent.relative_to(project_root).as_posix()
        for init in (project_root / "lintro").rglob("__init__.py")
    }


@pytest.mark.slow
def test_built_distributions_ship_the_whole_package_and_no_tests(
    built_distributions: Path,
) -> None:
    """Verify the find directive ships every subpackage and nothing extra.

    Guards ``[tool.setuptools.packages.find]`` (#1225): every source package
    must reach the wheel without a hand-maintained list, the PEP 561 marker
    must ship, and neither distribution may carry the repo-only test trees.

    Args:
        built_distributions: Directory holding the session's wheel and sdist.
    """
    wheels = list(built_distributions.glob("*.whl"))
    sdists = list(built_distributions.glob("*.tar.gz"))
    assert_that(wheels).is_not_empty()
    assert_that(sdists).is_not_empty()

    with zipfile.ZipFile(wheels[0]) as wheel:
        wheel_names = set(wheel.namelist())

        missing = sorted(
            package
            for package in _source_packages()
            if f"{package}/__init__.py" not in wheel_names
        )
        assert_that(missing).described_as(
            "packages missing from the wheel",
        ).is_empty()
        assert_that(wheel_names).contains("lintro/py.typed")
        # Declared package data, not modules: discovery never sees these, so
        # only [tool.setuptools.package-data] puts them in the wheel.
        project_root = Path(__file__).parent.parent.parent
        ascii_art = sorted(
            art.relative_to(project_root).as_posix()
            for art in (project_root / "lintro" / "ascii-art").glob("*.txt")
        )
        assert_that(ascii_art).is_not_empty()
        assert_that(wheel_names).contains(*ascii_art)

        # Only the package itself and its dist-info may sit at the top level.
        # ``lintro_build`` is checked by name because a prefix test would let the
        # in-tree build backend through: it also starts with "lintro".
        top_level = {name.split("/", maxsplit=1)[0] for name in wheel_names}
        assert_that(
            sorted(
                name
                for name in top_level
                if name != "lintro" and not name.endswith(".dist-info")
            ),
        ).described_as("unexpected top-level entries in the wheel").is_empty()
        assert_that(top_level).does_not_contain("lintro_build")

        with tarfile.open(sdists[0]) as sdist:
            # Drop the ``lintro-<version>/`` prefix every sdist member carries.
            sdist_paths = {
                name.split("/", maxsplit=1)[1]
                for name in sdist.getnames()
                if "/" in name
            }

        # evals/ is repo-only tooling that MANIFEST.in prunes (#2147).
        leaked = sorted(
            path
            for path in sdist_paths
            if path.startswith(("tests/", "test_samples/", "evals/"))
        )
        assert_that(leaked).described_as(
            "repo-only trees leaked into the sdist",
        ).is_empty()
        # The in-tree PEP 517 backend must survive the MANIFEST.in trim, or the
        # sdist cannot be built from.
        assert_that(sdist_paths).contains("lintro_build/backend.py", "lintro/py.typed")


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_built_sdist_installs_and_runs(built_distributions: Path) -> None:
    """Verify the sdist installs into a clean venv and the CLI runs.

    The sdist is what PyPI consumers build from when no wheel matches, so the
    trimmed MANIFEST.in must still carry everything the build needs.

    Args:
        built_distributions: Directory holding the session's wheel and sdist.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        venv_path = tmpdir_path / "test_venv"

        sdists = list(built_distributions.glob("*.tar.gz"))
        assert_that(sdists).is_not_empty()

        subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            [sys.executable, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        if sys.platform == "win32":
            bin_dir = venv_path / "Scripts"
            python_exe = bin_dir / "python.exe"
            cli_exe = bin_dir / "lintro.exe"
        else:
            bin_dir = venv_path / "bin"
            python_exe = bin_dir / "python"
            cli_exe = bin_dir / "lintro"

        install_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            [str(python_exe), "-m", "pip", "install", str(sdists[0])],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert_that(install_result.returncode).described_as(
            f"sdist install failed:\n{install_result.stdout}\n{install_result.stderr}",
        ).is_equal_to(0)

        smoke_checks = [
            "import lintro",
            "from lintro.utils.environment import collect_full_environment",
            "import pathlib, lintro; "
            "assert (pathlib.Path(lintro.__file__).parent / 'py.typed').is_file()",
        ]
        for statement in smoke_checks:
            check_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
                [str(python_exe), "-c", statement],
                # ``python -c`` puts the working directory first on sys.path,
                # so running from the checkout would import the source tree
                # instead of the freshly installed distribution.
                cwd=str(tmpdir_path),
                env=_isolated_env(),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert_that(check_result.returncode).described_as(
                f"Check failed: {statement}\n{check_result.stderr}",
            ).is_equal_to(0)

        version_result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            [str(cli_exe), "--version"],
            cwd=str(tmpdir_path),
            env=_isolated_env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert_that(version_result.returncode).is_equal_to(0)
        assert_that(version_result.stdout).contains("lintro")
