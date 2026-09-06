"""Tests for the shared Cargo workspace discovery helper (issue #2311).

``find_cargo_root`` decides which directory a Cargo command is launched from,
so its reconciliation rules — one package, several packages under a workspace,
several packages without one — are what the Rust definitions depend on.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.core.cargo import find_cargo_root


def _package(root: Path, name: str) -> Path:
    """Create a Cargo package with one source file under ``root``.

    Args:
        root: Directory to create the package in.
        name: Package name, used as both the directory and the crate name.

    Returns:
        Path to the package's ``src/lib.rs``.
    """
    package = root / name
    source = package / "src"
    source.mkdir(parents=True)
    (package / "Cargo.toml").write_text(f'[package]\nname = "{name}"\n')
    lib = source / "lib.rs"
    lib.write_text("pub fn f() {}\n")
    return lib


def test_a_file_resolves_to_its_own_package_root(tmp_path: Path) -> None:
    """A source file walks up to the directory owning its manifest.

    Args:
        tmp_path: Temporary directory for the package.
    """
    lib = _package(tmp_path, "demo")

    assert_that(find_cargo_root([str(lib)])).is_equal_to(tmp_path / "demo")


def test_a_directory_argument_is_searched_upward_too(tmp_path: Path) -> None:
    """Directories are accepted as well as files.

    Args:
        tmp_path: Temporary directory for the package.
    """
    lib = _package(tmp_path, "demo")

    assert_that(find_cargo_root([str(lib.parent)])).is_equal_to(tmp_path / "demo")


def test_paths_without_a_manifest_resolve_to_nothing(tmp_path: Path) -> None:
    """A tree with no ``Cargo.toml`` above it has no Cargo root.

    Args:
        tmp_path: Temporary directory holding a bare source file.
    """
    stray = tmp_path / "stray.rs"
    stray.write_text("fn main() {}\n")

    assert_that(find_cargo_root([str(stray)])).is_none()


def test_several_packages_resolve_to_their_workspace_root(tmp_path: Path) -> None:
    """Files in sibling crates share the workspace manifest above them.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["a", "b"]\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_several_packages_without_a_workspace_resolve_to_nothing(
    tmp_path: Path,
) -> None:
    """A common ancestor that owns no manifest is not a usable Cargo root.

    Args:
        tmp_path: Temporary directory holding two unrelated crates.
    """
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


def test_repeated_paths_in_one_package_stay_a_single_root(tmp_path: Path) -> None:
    """Several files from the same crate do not trigger the multi-root path.

    Args:
        tmp_path: Temporary directory for the package.
    """
    lib = _package(tmp_path, "demo")
    other = lib.parent / "other.rs"
    other.write_text("pub fn g() {}\n")

    resolved = find_cargo_root([str(lib), str(other)])

    assert_that(resolved).is_equal_to(tmp_path / "demo")


def test_roots_on_different_drives_resolve_to_nothing(tmp_path: Path) -> None:
    """An ancestor that cannot be computed at all is handled, not raised.

    ``os.path.commonpath`` raises on Windows paths spanning two drives; the
    helper turns that into the same "no usable root" answer.

    Args:
        tmp_path: Temporary directory holding two unrelated crates.
    """
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    with patch(
        "lintro.tools.core.cargo.os.path.commonpath",
        side_effect=ValueError("paths don't have the same drive"),
    ):
        resolved = find_cargo_root([str(first), str(second)], tool_label="rustfmt")

    assert_that(resolved).is_none()
