"""lintro's install destination agrees with the real uv and cargo (#2042).

Nothing is installed: uv creates a throwaway virtual environment and both
managers are asked read-only questions in temporary homes and projects. Each
test is skipped when its manager does not answer its version command.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - runs the real managers read-only with fixed argv
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.core.install_destination import (
    clear_probe_cache,
    install_destination,
)
from tests.integration._tools import NO_MIN_VERSION, require_command, unavailable

_UV = shutil.which("uv")
_CARGO = shutil.which("cargo")
#: Probe from the system temp directory, where no ``rust-toolchain`` file can
#: name a toolchain the rustup proxy would download; a proxy without a
#: default toolchain then fails the probe and the test is skipped.
_PROBE_CWD = tempfile.gettempdir()
_REQUIRES_UV = require_command(
    "uv",
    [_UV] if _UV else None,
    cwd=_PROBE_CWD,
    min_version=NO_MIN_VERSION,
)
_REQUIRES_CARGO = require_command(
    "cargo",
    [_CARGO] if _CARGO else None,
    cwd=_PROBE_CWD,
    min_version=NO_MIN_VERSION,
)
#: Variables kept from the caller. ``RUSTUP_*`` only pick the toolchain the
#: gate probed (and ``RUSTUP_HOME`` is where it lives); none moves cargo's
#: install root. ``UV_CACHE_DIR`` only relocates uv's cache.
_KEPT_VARS = frozenset({"UV_CACHE_DIR"})
#: Line uv logs for the environment it selected (lintro reads the same line).
_UV_ENVIRONMENT_LINE = re.compile(r"Using Python \S+ environment at: ")


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the user's manager configuration and earlier answers out.

    Every inherited ``UV_*`` and ``CARGO_*`` variable is dropped (for
    example ``UV_NO_CONFIG``, which would make uv ignore a test's
    ``uv.toml``, or ``CARGO_INSTALL_ROOT``), as are the active environment
    variables and the user and system configuration directories uv reads.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        None: Forgets cached manager answers once the test has finished.
    """
    for name in list(os.environ):
        if name.startswith(("UV_", "CARGO_")) and name not in _KEPT_VARS:
            monkeypatch.delenv(name)
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg-system"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    # A rustup proxy must never fetch a toolchain from inside a test.
    monkeypatch.setenv("RUSTUP_AUTO_INSTALL", "0")
    clear_probe_cache()
    yield
    clear_probe_cache()


def _toml_string(value: str) -> str:
    """Quote *value* as a TOML basic string.

    JSON string escapes are a subset of TOML's, so quotes, backslashes and
    control characters in a path survive.

    Args:
        value: Text to quote.

    Returns:
        The quoted string.
    """
    return json.dumps(value, ensure_ascii=False)


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


def _require_uv_environment_line(cwd: Path) -> None:
    """Skip unless this uv logs the selected environment the way lintro reads it.

    Args:
        cwd: Directory to ask uv in.
    """
    assert _UV is not None
    listing = subprocess.run(  # nosec B603 - fixed read-only argv, shell=False
        [_UV, "pip", "list", "--offline", "-v"],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
    )
    if not _UV_ENVIRONMENT_LINE.search(f"{listing.stdout}{listing.stderr}"):
        unavailable("this uv does not log 'Using Python ... environment at:'")


@_REQUIRES_UV
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
    _require_uv_environment_line(nested)

    destination = install_destination("uv pip install tool", cwd=nested)

    assert_that(destination.directory).is_not_none()
    expected = project / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    assert_that(os.path.samefile(str(destination.directory), expected)).is_true()


@_REQUIRES_UV
def test_uv_pip_configured_target_is_unknown(tmp_path: Path) -> None:
    """A ``[pip] target`` in uv.toml redirects the install; lintro names nothing.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    project = tmp_path / "project"
    _uv_venv(project / ".venv")
    target = _toml_string(str(tmp_path / "t"))
    (project / "uv.toml").write_text(f"[pip]\ntarget = {target}\n")

    destination = install_destination("uv pip install tool", cwd=project)

    assert_that(destination.directory).is_none()


@_REQUIRES_UV
def test_uv_working_dir_elsewhere_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With UV_WORKING_DIR, uv uses that project's .venv; lintro names nothing.

    uv logs the environment as ``.venv`` relative to UV_WORKING_DIR, while
    the install ran from another project that has a ``.venv`` of its own.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
    """
    ran_from = tmp_path / "a"
    elsewhere = tmp_path / "b"
    _uv_venv(ran_from / ".venv")
    _uv_venv(elsewhere / ".venv")
    _require_uv_environment_line(ran_from)
    monkeypatch.setenv("UV_WORKING_DIR", str(elsewhere))

    destination = install_destination("uv pip install tool", cwd=ran_from)

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


def _write_install_root(path: Path, root: Path) -> None:
    """Write a cargo config file that sets ``install.root``.

    Args:
        path: Config file to write.
        root: Install root it names.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[install]\nroot = {_toml_string(str(root))}\n")


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


@_REQUIRES_CARGO
@pytest.mark.parametrize(
    ("configs", "expected"),
    [
        (
            {
                "project/.cargo/config.toml": "proj-root",
                "a/cargo-home/config.toml": "toml-root",
                "a/cargo-home/config": "legacy-root",
            },
            "legacy-root",
        ),
        (
            {
                "a/cargo-home/config.toml": "toml-root",
                ".cargo/config.toml": "above-root",
            },
            "above-root",
        ),
        (
            {
                "a/cargo-home/config.toml": "toml-root",
                ".cargo/config.toml": "above-root",
                "a/.cargo/config.toml": "parent-root",
                "a/cargo-home/.cargo/config.toml": "nearest-root",
            },
            "nearest-root",
        ),
    ],
    ids=["home-legacy-config", "config-above-home", "nearest-config"],
)
def test_cargo_install_root_matches_cargo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configs: dict[str, str],
    expected: str,
) -> None:
    """Lintro and cargo pick the same ``install.root``.

    Cargo ignores project config, reads ``.cargo/config*`` from
    ``$CARGO_HOME`` upward (nearer wins), then ``$CARGO_HOME``'s own config,
    where the legacy ``config`` file beats ``config.toml``.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
        configs: Config files, relative to ``tmp_path``, and the root each sets.
        expected: Root cargo uses.
    """
    cargo_home = tmp_path / "a" / "cargo-home"
    project = tmp_path / "project"
    cargo_home.mkdir(parents=True)
    project.mkdir()
    for relative, root_name in configs.items():
        root = tmp_path / "roots" / root_name
        _crate_marker(root)
        _write_install_root(tmp_path / relative, root)
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))

    used = _cargo_install_root(project)
    destination = install_destination("cargo install tool", cwd=project)

    assert_that(used).is_equal_to(expected)
    assert_that(destination.directory).is_equal_to(
        tmp_path / "roots" / expected / "bin",
    )
