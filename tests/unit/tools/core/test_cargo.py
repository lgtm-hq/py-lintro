"""Tests for the shared Cargo workspace discovery helper (issue #2311).

``find_cargo_root`` decides which directory a Cargo command is launched from,
so its reconciliation rules — one package, several packages under a workspace,
several packages without one — are what the Rust definitions depend on.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
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


def test_an_ancestor_package_without_a_workspace_is_rejected(tmp_path: Path) -> None:
    """A parent ``[package]`` manifest is not a root for deeper packages.

    Running Cargo from the parent would build the parent crate alone, so a
    manifest that declares no ``[workspace]`` cannot stand in for the packages
    the files actually belong to.

    Args:
        tmp_path: Temporary directory used as the parent package.
    """
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "outer"\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


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


def test_nested_member_packages_resolve_to_the_outer_workspace_root(
    tmp_path: Path,
) -> None:
    """A nested member set resolves to the workspace root, not the member.

    The common ancestor of the two leaf packages is an intermediate
    ``[package]`` manifest that is itself a workspace member, so the walk has
    to continue upward until the ``[workspace]`` manifest is reached.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["outer"]\n')
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "Cargo.toml").write_text('[package]\nname = "outer"\n')
    first = _package(outer, "a")
    second = _package(outer, "b")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_members_below_a_shared_subdirectory_reach_the_workspace_root(
    tmp_path: Path,
) -> None:
    """A manifest-less common ancestor does not end the upward walk.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/a", "crates/b"]\n',
    )
    crates = tmp_path / "crates"
    crates.mkdir()
    first = _package(crates, "a")
    second = _package(crates, "b")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_workspace_manifest_that_is_also_a_package_is_accepted(
    tmp_path: Path,
) -> None:
    """A root manifest declaring both tables is still a workspace root.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "root"\n\n[workspace]\nmembers = ["a", "b"]\n',
    )
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_malformed_ancestor_manifest_is_not_treated_as_a_workspace(
    tmp_path: Path,
) -> None:
    """Unparseable TOML above the packages does not become the Cargo root.

    Args:
        tmp_path: Temporary directory holding the broken manifest.
    """
    (tmp_path / "Cargo.toml").write_text("[workspace\nmembers = broken\n")
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


@pytest.mark.parametrize("marker_is_file", [False, True], ids=["dir", "file"])
def test_the_upward_walk_stops_at_a_repository_boundary(
    tmp_path: Path,
    marker_is_file: bool,
) -> None:
    """A workspace manifest outside the repository is not adopted.

    Worktrees and submodules represent ``.git`` as a file, so both marker
    shapes must stop the walk.

    Args:
        tmp_path: Temporary directory holding the outer manifest.
        marker_is_file: Whether ``.git`` is a file (worktree) or a directory.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["repo/a"]\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    if marker_is_file:
        (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/repo\n")
    else:
        (repo / ".git").mkdir()
    first = _package(repo, "a")
    second = _package(repo, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


def test_a_non_utf8_ancestor_manifest_is_not_a_workspace(tmp_path: Path) -> None:
    """Invalid UTF-8 in a manifest is treated as "not a workspace", not raised.

    Args:
        tmp_path: Temporary directory holding the manifests.
    """
    (tmp_path / "Cargo.toml").write_bytes(b'[workspace]\nname = "\xff\xfe"\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()
