"""Tests for resolve-nuitka-version.py."""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 - subprocess drives the CLI under test; shell=False
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_LOCK_SNIPPET = """
version = 1

[[package]]
name = "ordered-set"
version = "4.1.0"

[[package]]
name = "Nuitka"
version = "4.1.3"

[[package]]
name = "zstandard"
version = "0.25.0"
"""


def _script_path() -> Path:
    """Return the resolve-nuitka-version.py script path.

    Returns:
        Absolute path to the script under test.
    """
    return (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "ci"
        / "resolve-nuitka-version.py"
    )


@pytest.fixture
def script_path() -> Path:
    """Provide the resolve-nuitka-version.py script path.

    Returns:
        Absolute path to the script under test.
    """
    return _script_path()


@pytest.fixture
def module() -> ModuleType:
    """Load resolve-nuitka-version.py as an importable module.

    Returns:
        The loaded module.

    Raises:
        RuntimeError: If the module spec cannot be created.
    """
    path = _script_path()
    spec = importlib.util.spec_from_file_location("resolve_nuitka_version", path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {path}"
        raise RuntimeError(msg)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def lockfile(tmp_path: Path) -> Path:
    """Write a minimal uv.lock fixture.

    Args:
        tmp_path: pytest-provided temporary directory.

    Returns:
        Path to the written lockfile.
    """
    path = tmp_path / "uv.lock"
    path.write_text(_LOCK_SNIPPET, encoding="utf-8")
    return path


def test_resolve_locked_version_reads_the_lockfile(
    module: ModuleType,
    lockfile: Path,
) -> None:
    """The locked version is read from the ``[[package]]`` table.

    Args:
        module: The loaded script module.
        lockfile: A minimal uv.lock fixture.
    """
    version = module.resolve_locked_version(lockfile=lockfile, package="nuitka")
    assert_that(version).is_equal_to("4.1.3")


def test_resolve_locked_version_matches_case_insensitively(
    module: ModuleType,
    lockfile: Path,
) -> None:
    """Lookup ignores the case of the distribution name.

    Args:
        module: The loaded script module.
        lockfile: A minimal uv.lock fixture.
    """
    version = module.resolve_locked_version(lockfile=lockfile, package="NUITKA")
    assert_that(version).is_equal_to("4.1.3")


def test_resolve_locked_version_rejects_unknown_packages(
    module: ModuleType,
    lockfile: Path,
) -> None:
    """A package absent from the lockfile raises ``ValueError``.

    Args:
        module: The loaded script module.
        lockfile: A minimal uv.lock fixture.
    """
    with pytest.raises(ValueError, match="not found"):
        module.resolve_locked_version(lockfile=lockfile, package="absent")


def test_resolve_locked_version_rejects_a_missing_lockfile(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    """A missing lockfile raises ``FileNotFoundError``.

    Args:
        module: The loaded script module.
        tmp_path: pytest-provided temporary directory.
    """
    with pytest.raises(FileNotFoundError):
        module.resolve_locked_version(
            lockfile=tmp_path / "absent.lock",
            package="nuitka",
        )


def test_main_writes_the_github_output(
    module: ModuleType,
    lockfile: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` appends the version to ``GITHUB_OUTPUT`` and prints it.

    Args:
        module: The loaded script module.
        lockfile: A minimal uv.lock fixture.
        tmp_path: pytest-provided temporary directory.
        monkeypatch: pytest environment patcher.
        capsys: stdout/stderr capture fixture.
    """
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    exit_code = module.main(["--lockfile", str(lockfile)])
    assert_that(exit_code).is_equal_to(0)
    assert_that(capsys.readouterr().out.strip()).is_equal_to("nuitka-version=4.1.3")
    assert_that(output.read_text(encoding="utf-8")).is_equal_to(
        "nuitka-version=4.1.3\n",
    )


def test_main_fails_on_a_missing_package(
    module: ModuleType,
    lockfile: Path,
) -> None:
    """``main`` returns 1 when the package is not in the lockfile.

    Args:
        module: The loaded script module.
        lockfile: A minimal uv.lock fixture.
    """
    exit_code = module.main(["--lockfile", str(lockfile), "--package", "absent"])
    assert_that(exit_code).is_equal_to(1)


def test_cli_resolves_the_repository_lockfile(script_path: Path) -> None:
    """The script resolves Nuitka from the repository's own uv.lock.

    Args:
        script_path: Path to the script under test.
    """
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [sys.executable, str(script_path), "--lockfile", str(repo_root / "uv.lock")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).starts_with("nuitka-version=")
    assert_that(result.stdout.strip()).is_not_equal_to("nuitka-version=")


def test_resolve_locked_version_rejects_invalid_toml(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    """A lockfile that is not valid TOML raises ``ValueError``.

    Args:
        module: The loaded script module.
        tmp_path: pytest-provided temporary directory.
    """
    path = tmp_path / "uv.lock"
    path.write_text("[[package]\nname = ", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid TOML"):
        module.resolve_locked_version(lockfile=path, package="nuitka")


def test_resolve_locked_version_rejects_a_version_less_entry(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    """A package entry without a version raises ``ValueError``.

    Args:
        module: The loaded script module.
        tmp_path: pytest-provided temporary directory.
    """
    path = tmp_path / "uv.lock"
    path.write_text('[[package]]\nname = "nuitka"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="has no version"):
        module.resolve_locked_version(lockfile=path, package="nuitka")
