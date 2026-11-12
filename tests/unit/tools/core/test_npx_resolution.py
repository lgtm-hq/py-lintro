"""Offline model of npx's exact-pin decision (#2043).

The match, workspace, symlink, alias, build-metadata, leading-``v``, dot-dir
and root-package layouts were each checked against real npx 11.20.0 offline,
with a localhost registry serving the pin; the expected values are what npx
did. The prerelease, scope, loop and bound cases follow semver and Arborist's
``load-actual.js`` rather than a live run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.npx_launch import NpxLaunch
from lintro.tools.core.npx_resolution import (
    decide_npx_launch,
    npm_project_root,
    npx_path_binary,
    tree_has_exact_version,
)

_PIN = "3.9.4"


def _manifest(directory: Path, data: dict[str, object]) -> Path:
    """Write ``package.json`` into *directory*.

    Args:
        directory: Directory to create.
        data: Manifest content.

    Returns:
        *directory*.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(json.dumps(data), encoding="utf-8")
    return directory


def _workspace(root: Path) -> Path:
    """Create a workspace root with member ``packages/app``.

    Args:
        root: Workspace root.

    Returns:
        The member directory.
    """
    _manifest(root, {"name": "ws", "private": True, "workspaces": ["packages/*"]})
    return _manifest(root / "packages" / "app", {"name": "app"})


# ---------------------------------------------------------------------------
# npm_project_root
# ---------------------------------------------------------------------------


def test_project_root_is_the_nearest_package_json(tmp_path: Path) -> None:
    """A plain package is its own root, even from a subdirectory.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    project = _manifest(tmp_path / "proj", {"name": "proj"})
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)

    assert_that(npm_project_root(nested)).is_equal_to(project.resolve())


def test_project_root_accepts_a_bare_node_modules_directory(tmp_path: Path) -> None:
    """A ``node_modules`` directory marks the root without a ``package.json``.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "proj" / "node_modules").mkdir(parents=True)
    nested = tmp_path / "proj" / "src"
    nested.mkdir()

    assert_that(npm_project_root(nested)).is_equal_to((tmp_path / "proj").resolve())


def test_project_root_is_promoted_to_the_workspace_root(tmp_path: Path) -> None:
    """A workspace member resolves to the root that lists it.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    app = _workspace(tmp_path / "ws")

    assert_that(npm_project_root(app)).is_equal_to((tmp_path / "ws").resolve())


def test_project_root_ignores_a_workspace_root_that_does_not_list_it(
    tmp_path: Path,
) -> None:
    """A package outside the root's ``workspaces`` stays its own root.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _workspace(tmp_path / "ws")
    tool = _manifest(tmp_path / "ws" / "tools" / "gen", {"name": "gen"})

    assert_that(npm_project_root(tool)).is_equal_to(tool.resolve())


def test_project_root_without_markers_is_the_directory_itself(tmp_path: Path) -> None:
    """With no marker anywhere above, npm uses the working directory.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    bare = tmp_path / "bare"
    bare.mkdir()

    root = npm_project_root(bare)

    assert_that(root == bare.resolve() or (root / "package.json").exists()).is_true()


# ---------------------------------------------------------------------------
# tree_has_exact_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "manifest", "expected"),
    [
        ("node_modules/prettier", {"name": "prettier", "version": "3.9.3"}, False),
        (
            "node_modules/other/node_modules/prettier",
            {"name": "prettier", "version": _PIN},
            True,
        ),
        ("node_modules/prettier-alias", {"name": "prettier", "version": _PIN}, True),
        ("node_modules/prettier", {"version": _PIN}, False),
        (
            "node_modules/x/node_modules/prettier",
            {"name": "prettier", "version": f"{_PIN}+meta"},
            True,
        ),
        (
            "node_modules/x/node_modules/prettier",
            {"name": "prettier", "version": f"v{_PIN}"},
            True,
        ),
        (
            "node_modules/x/node_modules/prettier",
            {"name": "prettier", "version": f"{_PIN}-rc.1"},
            False,
        ),
        (
            "node_modules/.pnpm/prettier@3.9.4/node_modules/prettier",
            {"name": "prettier", "version": _PIN},
            False,
        ),
        (
            "node_modules/@scope/wrap/node_modules/prettier",
            {"name": "prettier", "version": _PIN},
            True,
        ),
        (
            "packages/app/node_modules/prettier",
            {"name": "prettier", "version": _PIN},
            True,
        ),
    ],
    ids=[
        "older-only",
        "nested-exact",
        "alias-folder",
        "no-name",
        "build-metadata",
        "leading-v",
        "prerelease-differs",
        "unlinked-dot-store",
        "under-scope",
        "unlinked-workspace-member",
    ],
)
def test_tree_match_follows_npx(
    tmp_path: Path,
    relative: str,
    manifest: dict[str, object],
    expected: bool,
) -> None:
    """Name and semver-equal version decide a match, wherever npm loads it.

    Args:
        tmp_path: Temporary directory provided by pytest.
        relative: Package directory relative to the workspace root.
        manifest: That package's ``package.json``.
        expected: Whether npx found the pin in that layout.
    """
    root = tmp_path / "ws"
    _workspace(root)
    _manifest(root / relative, manifest)

    assert_that(tree_has_exact_version(root, "prettier", _PIN)).is_equal_to(expected)


def test_root_package_itself_can_be_the_match(tmp_path: Path) -> None:
    """Developing the tool itself counts as having the pin installed.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    root = _manifest(tmp_path / "prettier", {"name": "prettier", "version": _PIN})

    assert_that(tree_has_exact_version(root, "prettier", _PIN)).is_true()


def test_symlink_into_a_dot_store_is_followed(tmp_path: Path) -> None:
    """A pnpm-style link reaches a store copy the walk would otherwise skip.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    root = tmp_path / "ws"
    _workspace(root)
    store = _manifest(
        root
        / "node_modules"
        / ".pnpm"
        / "prettier@3.9.4"
        / "node_modules"
        / "prettier",
        {"name": "prettier", "version": _PIN},
    )
    holder = _manifest(root / "node_modules" / "other", {"name": "other"})
    (holder / "node_modules").mkdir()
    (holder / "node_modules" / "prettier").symlink_to(store)

    assert_that(tree_has_exact_version(root, "prettier", _PIN)).is_true()


def test_link_target_outside_the_root_is_walked(tmp_path: Path) -> None:
    """A ``file:`` link leaving the root still contributes its subtree.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    root = tmp_path / "ws"
    _workspace(root)
    outside = _manifest(tmp_path / "outside-lib", {"name": "lib"})
    _manifest(
        outside / "node_modules" / "prettier",
        {"name": "prettier", "version": _PIN},
    )
    (root / "node_modules").mkdir()
    (root / "node_modules" / "lib").symlink_to(outside)

    assert_that(tree_has_exact_version(root, "prettier", _PIN)).is_true()


def test_package_beside_the_root_is_not_in_its_tree(tmp_path: Path) -> None:
    """The root's parent ``node_modules`` is outside npm's tree.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _manifest(
        tmp_path / "node_modules" / "prettier",
        {"name": "prettier", "version": _PIN},
    )
    root = _manifest(tmp_path / "proj", {"name": "proj"})

    assert_that(tree_has_exact_version(root, "prettier", _PIN)).is_false()


def test_symlink_loop_terminates(tmp_path: Path) -> None:
    """A package linking back to an ancestor is visited once.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    root = tmp_path / "ws"
    _workspace(root)
    loop = _manifest(root / "node_modules" / "a", {"name": "a"})
    (loop / "node_modules").mkdir()
    (loop / "node_modules" / "back").symlink_to(root)

    assert_that(tree_has_exact_version(root, "prettier", _PIN)).is_false()


def test_tree_over_the_bound_is_undecided(tmp_path: Path) -> None:
    """Exceeding the package budget yields None, never a guessed answer.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    root = tmp_path / "ws"
    _workspace(root)
    for index in range(5):
        _manifest(root / "node_modules" / f"p{index}", {"name": f"p{index}"})

    assert_that(
        tree_has_exact_version(root, "prettier", _PIN, max_packages=3),
    ).is_none()


# ---------------------------------------------------------------------------
# npx_path_binary and decide_npx_launch
# ---------------------------------------------------------------------------


def _bin(directory: Path) -> Path:
    """Create an executable ``node_modules/.bin/prettier`` under *directory*.

    Args:
        directory: Directory owning the ``node_modules``.

    Returns:
        The executable.
    """
    entry = directory / "node_modules" / ".bin" / "prettier"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("#!/bin/sh\n", encoding="utf-8")
    entry.chmod(0o755)
    return entry


def test_path_binary_is_the_nearest_ancestor_entry(tmp_path: Path) -> None:
    """The PATH npx builds lists ``node_modules/.bin`` nearest first, unbounded.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _bin(tmp_path)
    near = _bin(tmp_path / "a")
    start = tmp_path / "a" / "b"
    start.mkdir()

    assert_that(npx_path_binary("prettier", start)).is_equal_to(
        near.resolve().as_posix(),
    )


def test_path_binary_skips_a_non_executable_entry(tmp_path: Path) -> None:
    """A plain file is not runnable by name, so the next ancestor wins.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    far = _bin(tmp_path)
    near = _bin(tmp_path / "a")
    near.chmod(0o644)

    assert_that(npx_path_binary("prettier", tmp_path / "a")).is_equal_to(
        far.resolve().as_posix(),
    )


@pytest.mark.parametrize(
    ("pin_installed", "bin_reachable", "launch"),
    [
        (False, True, NpxLaunch.PINNED),
        (True, True, NpxLaunch.LOCAL),
        (True, False, NpxLaunch.NO_BIN),
    ],
    ids=["no-pin", "pin-and-bin", "pin-no-bin"],
)
def test_decision_combines_tree_and_path(
    tmp_path: Path,
    pin_installed: bool,
    bin_reachable: bool,
    launch: NpxLaunch,
) -> None:
    """Only an installed pin makes npx look up the binary by name.

    Args:
        tmp_path: Temporary directory provided by pytest.
        pin_installed: Whether the pin is somewhere in the tree.
        bin_reachable: Whether an ancestor ``.bin/prettier`` exists.
        launch: Expected prediction.
    """
    root = tmp_path / "ws"
    app = _workspace(root)
    if pin_installed:
        _manifest(
            root / "node_modules" / "w" / "node_modules" / "prettier",
            {"name": "prettier", "version": _PIN},
        )
    entry = _bin(root) if bin_reachable else None

    decision = decide_npx_launch(
        package="prettier",
        version=_PIN,
        binary="prettier",
        cwd=app,
    )

    assert_that(decision.launch).is_equal_to(launch)
    assert_that(decision.project_root).is_equal_to(root.resolve())
    if launch is NpxLaunch.LOCAL:
        assert entry is not None
        assert_that(decision.binary).is_equal_to(entry.resolve().as_posix())
    if launch is NpxLaunch.NO_BIN:
        assert_that(decision.reason).contains("but no prettier on its PATH")


def test_decision_over_the_bound_is_undecided_with_a_reason(tmp_path: Path) -> None:
    """An unsettled tree fails closed with an explanation.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    root = tmp_path / "ws"
    app = _workspace(root)
    for index in range(5):
        _manifest(root / "node_modules" / f"p{index}", {"name": f"p{index}"})

    decision = decide_npx_launch(
        package="prettier",
        version=_PIN,
        binary="prettier",
        cwd=app,
        max_packages=3,
    )

    assert_that(decision.launch).is_equal_to(NpxLaunch.UNDECIDED)
    assert_that(decision.reason).contains("more than 3 packages")
