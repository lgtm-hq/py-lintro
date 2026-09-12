"""Tests for the shared Cargo workspace discovery helper (issue #2311).

``find_cargo_root`` decides which directory a Cargo command is launched from,
so its reconciliation rules — one package, several packages under a workspace,
several packages without one — are what the Rust definitions depend on.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.core.cargo import (
    CargoRoot,
    CargoRootIssue,
    cargo_package_args,
    find_cargo_root,
    resolve_cargo_root,
)


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
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["outer", "outer/a", "outer/b"]\n',
    )
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

    The outer manifest lists both packages, so it would be adopted on
    membership alone; only the repository boundary keeps it out. Worktrees
    and submodules represent ``.git`` as a file, so both marker shapes must
    stop the walk.

    Args:
        tmp_path: Temporary directory holding the outer manifest.
        marker_is_file: Whether ``.git`` is a file (worktree) or a directory.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["repo/a", "repo/b"]\n',
    )
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


def test_a_scalar_workspace_key_is_not_a_workspace(tmp_path: Path) -> None:
    """Only a ``[workspace]`` table declares a workspace, not a scalar key.

    Args:
        tmp_path: Temporary directory holding the manifests.
    """
    (tmp_path / "Cargo.toml").write_text('workspace = "value"\n[package]\nname = "x"\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


def test_a_workspace_root_that_also_holds_git_is_adopted(tmp_path: Path) -> None:
    """The manifest check runs before the repository-boundary check.

    Every real repository keeps ``.git`` next to the workspace manifest, so
    the walk must adopt that directory rather than stop at it.

    Args:
        tmp_path: Temporary directory holding the workspace.
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["a", "b"]\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_equal_to(
        tmp_path.resolve(),
    )


def test_packages_in_sibling_repositories_resolve_to_nothing(tmp_path: Path) -> None:
    """A workspace manifest above two repositories is unrelated to both.

    Args:
        tmp_path: Temporary directory holding both repositories.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["one/a"]\n')
    first_repo = tmp_path / "one"
    second_repo = tmp_path / "two"
    for repo in (first_repo, second_repo):
        repo.mkdir()
        (repo / ".git").mkdir()
    first = _package(first_repo, "a")
    second = _package(second_repo, "b")

    resolved = find_cargo_root([str(first), str(second)], tool_label="rustfmt")

    assert_that(resolved).is_none()


def test_a_package_outside_the_repository_is_not_joined_to_one_inside(
    tmp_path: Path,
) -> None:
    """A repository crate and an unversioned crate share no Cargo root.

    Args:
        tmp_path: Temporary directory holding both crates.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["repo/a", "b"]\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    first = _package(repo, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


def test_a_member_that_is_its_own_repository_still_reaches_the_workspace(
    tmp_path: Path,
) -> None:
    """A submodule member does not cut the walk short of the workspace root.

    The member carries its own ``.git``, so its nearest repository is not the
    workspace's; the shared outer repository is what bounds the walk.

    Args:
        tmp_path: Temporary directory used as the workspace repository.
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["outer", "outer/a", "outer/b"]\n',
    )
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "Cargo.toml").write_text('[package]\nname = "outer"\n')
    first = _package(outer, "a")
    second = _package(outer, "b")
    (first.parent.parent / ".git").mkdir()

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_an_excluded_subtree_is_not_owned_by_the_workspace(tmp_path: Path) -> None:
    """A workspace that excludes the packages is not their Cargo root.

    Args:
        tmp_path: Temporary directory holding the workspace.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["one"]\nexclude = ["two/"]\n',
    )
    _package(tmp_path, "one")
    outside = tmp_path / "two"
    outside.mkdir()
    first = _package(outside, "a")
    second = _package(outside, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


def test_members_matched_by_a_glob_are_owned(tmp_path: Path) -> None:
    """``members`` entries are globs, so ``crates/*`` covers each crate.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n')
    crates = tmp_path / "crates"
    crates.mkdir()
    first = _package(crates, "a")
    second = _package(crates, "b")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_the_root_package_counts_as_a_member_of_its_own_workspace(
    tmp_path: Path,
) -> None:
    """A manifest with both tables owns its own directory as well.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "root"\n\n[workspace]\nmembers = ["a"]\n',
    )
    source = tmp_path / "src"
    source.mkdir()
    root_lib = source / "lib.rs"
    root_lib.write_text("pub fn f() {}\n")
    member = _package(tmp_path, "a")

    resolved = find_cargo_root([str(root_lib), str(member)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_workspace_that_lists_other_members_is_walked_past(tmp_path: Path) -> None:
    """The walk continues when the nearest workspace does not own the roots.

    Args:
        tmp_path: Temporary directory used as the outer workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["inner/a", "inner/b"]\n',
    )
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "Cargo.toml").write_text('[workspace]\nmembers = ["x"]\n')
    _package(inner, "x")
    first = _package(inner, "a")
    second = _package(inner, "b")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_path_dependency_inside_the_workspace_is_a_member(tmp_path: Path) -> None:
    """Cargo makes an in-workspace path dependency a member, so lintro does.

    The manifest carries no ``members`` key at all; the crate is reachable
    only as a ``path`` dependency of the root package.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "root"\n\n[workspace]\n\n'
        '[dependencies]\nhelper = { path = "helper" }\n',
    )
    source = tmp_path / "src"
    source.mkdir()
    root_lib = source / "lib.rs"
    root_lib.write_text("pub fn f() {}\n")
    helper = _package(tmp_path, "helper")

    resolved = find_cargo_root([str(root_lib), str(helper)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_dev_dependency_path_is_followed_transitively(tmp_path: Path) -> None:
    """Every dependency table counts, and the walk follows them onward.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["a"]\n',
    )
    first = _package(tmp_path, "a")
    (tmp_path / "a" / "Cargo.toml").write_text(
        '[package]\nname = "a"\n\n[dev-dependencies]\nb = { path = "../b" }\n',
    )
    second = _package(tmp_path, "b")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_path_dependency_outside_the_workspace_is_not_followed(
    tmp_path: Path,
) -> None:
    """A dependency outside the workspace directory does not carry members in.

    The outside crate depends back on ``ws/b``, so following it would make
    ``b`` a member of ``ws`` even though cargo would not.

    Args:
        tmp_path: Temporary directory holding the workspace and the outsider.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text(
        '[package]\nname = "root"\n\n[workspace]\nmembers = ["a"]\n\n'
        '[dependencies]\noutside = { path = "../outside" }\n',
    )
    first = _package(workspace, "a")
    second = _package(workspace, "b")
    outside = _package(tmp_path, "outside")
    (outside.parent.parent / "Cargo.toml").write_text(
        '[package]\nname = "outside"\n\n[dependencies]\nb = { path = "../ws/b" }\n',
    )

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


def test_an_excluded_path_dependency_is_not_a_member(tmp_path: Path) -> None:
    """``exclude`` wins over reachability through a path dependency.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "root"\n\n[workspace]\nmembers = ["a"]\n'
        'exclude = ["vendored"]\n\n'
        '[build-dependencies]\nv = { path = "vendored" }\n',
    )
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "vendored")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


def test_an_unexpandable_member_pattern_matches_nothing(tmp_path: Path) -> None:
    """A pattern the platform cannot expand is not an error.

    ``..`` in a glob is rejected outright by some Python versions and simply
    matches nothing on others; either way the workspace owns no members.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["../shared/*"]\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_none()


def test_package_args_name_each_input_package(tmp_path: Path) -> None:
    """A workspace root is given an explicit package selection.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["a", "b"]\ndefault-members = ["a"]\n',
    )
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    args = cargo_package_args([str(first), str(second)], tmp_path.resolve())

    assert_that(args).is_equal_to(["-p", "a", "-p", "b"])


def test_package_args_are_empty_for_a_single_package(tmp_path: Path) -> None:
    """A plain package root needs no selection.

    Args:
        tmp_path: Temporary directory holding the package.
    """
    lib = _package(tmp_path, "demo")

    args = cargo_package_args([str(lib)], (tmp_path / "demo").resolve())

    assert_that(args).is_empty()


def test_package_args_fall_back_to_the_whole_workspace(tmp_path: Path) -> None:
    """An unreadable package name widens the selection instead of dropping it.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["a", "b"]\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")
    (tmp_path / "b" / "Cargo.toml").write_text('[package]\nedition = "2021"\n')

    args = cargo_package_args([str(first), str(second)], tmp_path.resolve())

    assert_that(args).is_equal_to(["--workspace"])


def test_a_virtual_workspace_owns_its_own_root_manifest(tmp_path: Path) -> None:
    """The root ``Cargo.toml`` is an input, and the workspace owns it.

    Clippy's file patterns (``*.rs`` and ``Cargo.toml``) always hand the
    workspace's own manifest to discovery, so the workspace directory is one
    of the input roots. A virtual manifest declares no ``[package]``, so it
    would not own itself unless the manifest's own directory counts as a
    member outright.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[workspace]\nmembers = ["crates/a", "crates/b"]\n')
    crates = tmp_path / "crates"
    crates.mkdir()
    first = _package(crates, "a")
    second = _package(crates, "b")

    resolved = find_cargo_root([str(manifest), str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_virtual_workspace_owns_the_cargo_deny_input_shape(tmp_path: Path) -> None:
    """``cargo deny`` discovery hands over every manifest plus ``deny.toml``.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[workspace]\nmembers = ["crates/a", "crates/b"]\n')
    deny = tmp_path / "deny.toml"
    deny.write_text('[bans]\nmultiple-versions = "warn"\n')
    crates = tmp_path / "crates"
    crates.mkdir()
    first = _package(crates, "a")
    second = _package(crates, "b")

    resolved = find_cargo_root(
        [
            str(manifest),
            str(deny),
            str(first.parent.parent / "Cargo.toml"),
            str(second.parent.parent / "Cargo.toml"),
        ],
    )

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_virtual_workspace_still_resolves_to_the_common_ancestor(
    tmp_path: Path,
) -> None:
    """The pre-workspace-check answer is preserved for the common layout.

    Before ownership was checked, a multi-root input resolved to the common
    ancestor of its roots. For a virtual workspace whose own manifest is an
    input, that ancestor is the workspace root, and it has to stay the answer.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[workspace]\nmembers = ["crates/a", "crates/b"]\n')
    crates = tmp_path / "crates"
    crates.mkdir()
    first = _package(crates, "a")
    second = _package(crates, "b")
    inputs = [str(manifest), str(first), str(second)]
    ancestor = Path(
        os.path.commonpath([str(tmp_path.resolve()), str(first), str(second)]),
    )

    assert_that(find_cargo_root(inputs)).is_equal_to(ancestor)


def test_package_args_widen_to_the_workspace_for_a_virtual_root(
    tmp_path: Path,
) -> None:
    """A virtual root has no package name, so the selection is the workspace.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[workspace]\nmembers = ["crates/a"]\n')
    crates = tmp_path / "crates"
    crates.mkdir()
    first = _package(crates, "a")

    args = cargo_package_args([str(manifest), str(first)], tmp_path.resolve())

    assert_that(args).is_equal_to(["--workspace"])


def test_an_inherited_workspace_dependency_path_is_followed(tmp_path: Path) -> None:
    """``workspace = true`` keeps the path in ``[workspace.dependencies]``.

    The member manifest names no path at all, so the helper crate is a member
    only if the workspace's own dependency table is consulted.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["a"]\n\n'
        '[workspace.dependencies]\nhelper = { path = "helper" }\n',
    )
    first = _package(tmp_path, "a")
    (tmp_path / "a" / "Cargo.toml").write_text(
        '[package]\nname = "a"\n\n[dependencies]\nhelper = { workspace = true }\n',
    )
    helper = _package(tmp_path, "helper")

    resolved = find_cargo_root([str(first), str(helper)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_target_specific_dependency_path_is_followed(tmp_path: Path) -> None:
    """A ``[target.'cfg(...)'.dependencies]`` path carries a member in too.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["a"]\n')
    first = _package(tmp_path, "a")
    (tmp_path / "a" / "Cargo.toml").write_text(
        '[package]\nname = "a"\n\n'
        '[target."cfg(unix)".dependencies]\nb = { path = "../b" }\n',
    )
    second = _package(tmp_path, "b")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_an_absolute_member_path_stays_absolute(tmp_path: Path) -> None:
    """Cargo reads a leading ``/`` as an absolute path, so lintro does too.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")
    members = ", ".join(
        f'"{(tmp_path / name).resolve().as_posix()}"' for name in ("a", "b")
    )
    (tmp_path / "Cargo.toml").write_text(f"[workspace]\nmembers = [{members}]\n")

    resolved = find_cargo_root([str(first), str(second)])

    assert_that(resolved).is_equal_to(tmp_path.resolve())


def test_a_missing_manifest_is_reported_as_the_skip_reason(tmp_path: Path) -> None:
    """No manifest anywhere keeps the original wording.

    Args:
        tmp_path: Temporary directory holding a bare source file.
    """
    stray = tmp_path / "stray.rs"
    stray.write_text("fn main() {}\n")

    resolved = resolve_cargo_root([str(stray)])

    assert_that(resolved.issue).is_equal_to(CargoRootIssue.NO_MANIFEST)
    assert_that(resolved.skip_message("clippy")).is_equal_to(
        "No Cargo.toml found; skipping clippy.",
    )


def test_separate_drives_are_reported_as_the_skip_reason(tmp_path: Path) -> None:
    """An uncomputable common ancestor names the drives in the message.

    Args:
        tmp_path: Temporary directory holding two unrelated crates.
    """
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    with patch(
        "lintro.tools.core.cargo.os.path.commonpath",
        side_effect=ValueError("paths don't have the same drive"),
    ):
        resolved = resolve_cargo_root([str(first), str(second)])

    assert_that(resolved.issue).is_equal_to(CargoRootIssue.SEPARATE_DRIVES)
    assert_that(resolved.skip_message("clippy")).contains("separate drives")
    assert_that(resolved.skip_message("clippy")).ends_with("skipping clippy.")


def test_split_repositories_are_reported_as_the_skip_reason(tmp_path: Path) -> None:
    """Crates in sibling repositories say so rather than claim no manifest.

    Args:
        tmp_path: Temporary directory holding both repositories.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["one/a"]\n')
    for name in ("one", "two"):
        repository = tmp_path / name
        repository.mkdir()
        (repository / ".git").mkdir()
    first = _package(tmp_path / "one", "a")
    second = _package(tmp_path / "two", "b")

    resolved = resolve_cargo_root([str(first), str(second)])

    assert_that(resolved.issue).is_equal_to(CargoRootIssue.SPLIT_REPOSITORIES)
    assert_that(resolved.skip_message("clippy")).contains("separate repositories")
    assert_that(resolved.skip_message("clippy")).ends_with("skipping clippy.")


def test_a_package_only_ancestor_is_reported_as_the_skip_reason(
    tmp_path: Path,
) -> None:
    """A ``[package]``-only ancestor names itself in the message.

    Args:
        tmp_path: Temporary directory holding the package-only ancestor.
    """
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "outer"\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    resolved = resolve_cargo_root([str(first), str(second)])

    assert_that(resolved.issue).is_equal_to(CargoRootIssue.PACKAGE_ONLY_ANCESTOR)
    assert_that(resolved.skip_message("clippy")).contains("only a package")
    assert_that(resolved.skip_message("clippy")).ends_with("skipping clippy.")


def test_a_repository_boundary_stop_is_reported_as_the_skip_reason(
    tmp_path: Path,
) -> None:
    """Stopping at ``.git`` says the repository holds no owning workspace.

    Args:
        tmp_path: Temporary directory holding the outer manifest.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["repo/a", "repo/b"]\n',
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    first = _package(repository, "a")
    second = _package(repository, "b")

    resolved = resolve_cargo_root([str(first), str(second)])

    assert_that(resolved.issue).is_equal_to(CargoRootIssue.REPOSITORY_BOUNDARY)
    assert_that(resolved.skip_message("clippy")).contains("inside the repository")
    assert_that(resolved.skip_message("clippy")).ends_with("skipping clippy.")


def test_an_unowned_member_set_is_reported_as_the_skip_reason(
    tmp_path: Path,
) -> None:
    """A workspace listing other crates says nothing owns the inputs.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["x"]\n')
    _package(tmp_path, "x")
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    resolved = resolve_cargo_root([str(first), str(second)])

    assert_that(resolved.issue).is_equal_to(CargoRootIssue.NO_OWNING_WORKSPACE)
    assert_that(resolved.skip_message("clippy")).contains(
        "No workspace Cargo.toml owns every Cargo root",
    )


def test_a_resolved_root_falls_back_to_the_generic_skip_message() -> None:
    """A root with no recorded reason still produces a usable sentence.

    ``skip_message`` is only read when no root was found, so the fallback is
    defensive; it still has to name the tool rather than raise.
    """
    resolved = CargoRoot(root=Path("/tmp"), issue=None)

    assert_that(resolved.skip_message("clippy")).is_equal_to(
        "No Cargo.toml found; skipping clippy.",
    )


def test_a_bare_root_member_pattern_is_ignored(tmp_path: Path) -> None:
    """A member entry naming the filesystem root contributes no member.

    Args:
        tmp_path: Temporary directory used as the workspace root.
    """
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["/", "a", "b"]\n')
    first = _package(tmp_path, "a")
    second = _package(tmp_path, "b")

    assert_that(find_cargo_root([str(first), str(second)])).is_equal_to(
        tmp_path.resolve(),
    )


def test_a_package_only_ancestor_at_the_boundary_is_reported(tmp_path: Path) -> None:
    """Hitting ``.git`` on a ``[package]``-only manifest names the package.

    The repository boundary and the package-only ancestor coincide, and the
    more specific reason is the one reported.

    Args:
        tmp_path: Temporary directory holding the repository.
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "Cargo.toml").write_text('[package]\nname = "outer"\n')
    first = _package(repository, "a")
    second = _package(repository, "b")

    resolved = resolve_cargo_root([str(first), str(second)])

    assert_that(resolved.issue).is_equal_to(CargoRootIssue.PACKAGE_ONLY_ANCESTOR)
    assert_that(resolved.skip_message("cargo-deny")).contains("only a package")
    assert_that(resolved.skip_message("cargo-deny")).ends_with("skipping cargo-deny.")
