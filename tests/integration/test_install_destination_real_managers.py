"""lintro's install destination agrees with the real uv and cargo (#2042).

Nothing is installed: uv creates a throwaway virtual environment and both
managers are asked read-only questions in temporary homes and projects. Each
test is skipped when its manager is not on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - runs the real managers read-only with fixed argv
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.core.install_destination import (
    clear_probe_cache,
    install_destination,
)

_UV = shutil.which("uv")
_CARGO = shutil.which("cargo")
_ISOLATED_VARS = (
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "UV_PYTHON",
    "UV_SYSTEM_PYTHON",
    "UV_PROJECT",
    "UV_WORKING_DIR",
    "UV_CONFIG_FILE",
    "CARGO_INSTALL_ROOT",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the user's manager configuration and earlier answers out.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        None: Forgets cached manager answers once the test has finished.
    """
    for name in _ISOLATED_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    clear_probe_cache()
    yield
    clear_probe_cache()


def _uv_venv(path: Path) -> None:
    """Create a virtual environment with the real uv, offline.

    Args:
        path: Environment root.
    """
    assert _UV is not None
    subprocess.run(  # nosec B603 - fixed argv, shell=False
        [_UV, "venv", "-q", "--offline", "--python", sys.executable, str(path)],
        check=True,
        capture_output=True,
        timeout=60,
    )


@pytest.mark.skipif(_UV is None, reason="uv is not on PATH")
def test_uv_pip_skips_a_missing_virtual_env_like_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A VIRTUAL_ENV that does not exist is skipped; the project .venv is named.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
    """
    project = tmp_path / "project"
    _uv_venv(project / ".venv")
    nested = project / "pkg"
    nested.mkdir()
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "missing"))

    destination = install_destination("uv pip install tool", cwd=nested)

    assert_that(destination.directory).is_not_none()
    expected = project / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    assert_that(os.path.samefile(str(destination.directory), expected)).is_true()


@pytest.mark.skipif(_UV is None, reason="uv is not on PATH")
def test_uv_pip_configured_target_is_unknown(tmp_path: Path) -> None:
    """A ``[pip] target`` in uv.toml redirects the install; lintro names nothing.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    project = tmp_path / "project"
    _uv_venv(project / ".venv")
    (project / "uv.toml").write_text(f'[pip]\ntarget = "{tmp_path / "t"}"\n')

    destination = install_destination("uv pip install tool", cwd=project)

    assert_that(destination.directory).is_none()


def _crate_marker(root: Path) -> None:
    """Record a fake installed crate named after *root* in cargo's ledger.

    Args:
        root: Install root cargo might list.
    """
    root.mkdir(parents=True)
    crate = root.name
    (root / ".crates.toml").write_text(
        "[v1]\n"
        f'"{crate} 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)"'
        f' = ["{crate}"]\n',
    )


def _cargo_install_root(cwd: Path) -> str:
    """Ask the real cargo which install root it uses, through its ledger.

    Args:
        cwd: Directory to run cargo in.

    Returns:
        The single crate ``cargo install --list`` reports, which names its
        root.
    """
    assert _CARGO is not None
    listing = subprocess.run(  # nosec B603 - fixed read-only argv, shell=False
        [_CARGO, "install", "--list"],
        check=True,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
    )
    crates = [
        line.split()[0] for line in listing.stdout.splitlines() if line[:1] != " "
    ]
    assert_that(crates).is_length(1)
    return crates[0]


@pytest.mark.skipif(_CARGO is None, reason="cargo is not on PATH")
def test_cargo_install_root_matches_cargo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project config is ignored and the legacy ``config`` file wins, as in cargo.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cargo_home = tmp_path / "cargo-home"
    project = tmp_path / "project"
    roots = {
        name: tmp_path / name for name in ("proj-root", "toml-root", "legacy-root")
    }
    for root in roots.values():
        _crate_marker(root)
    (project / ".cargo").mkdir(parents=True)
    cargo_home.mkdir()
    for path, root in (
        (project / ".cargo" / "config.toml", roots["proj-root"]),
        (cargo_home / "config.toml", roots["toml-root"]),
        (cargo_home / "config", roots["legacy-root"]),
    ):
        path.write_text(f"[install]\nroot = {str(root)!r}\n")
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))

    used = _cargo_install_root(project)
    destination = install_destination("cargo install tool", cwd=project)

    assert_that(used).is_equal_to("legacy-root")
    assert_that(destination.directory).is_equal_to(roots["legacy-root"] / "bin")
