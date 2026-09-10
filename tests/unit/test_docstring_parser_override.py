"""Keep ``docstring-parser`` out of the resolution so pydoclint stays importable.

``pydoclint`` (the ``full`` extra) needs ``docstring-parser-fork`` and ``anthropic``
(the ``ai`` extra) needs ``docstring-parser``. Both distributions install the same
top-level ``docstring_parser`` package, so installing both leaves one shadowing the
other and pydoclint dies on import — which ``lintro chk`` reports as a *skip*, not a
failure, silently dropping the DOC gate. ``[tool.uv] override-dependencies`` in
``pyproject.toml`` drops anthropic's requirement and lets the fork own the module.
See issue #2378.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_UV_LOCK = _REPO_ROOT / "uv.lock"
_OVERRIDE = "docstring-parser ; sys_platform == 'never'"


def _lock_packages() -> list[dict[str, Any]]:
    """Return the ``[[package]]`` entries recorded in ``uv.lock``.

    Returns:
        The parsed package tables, in lockfile order.
    """
    data: dict[str, Any] = tomllib.loads(_UV_LOCK.read_text(encoding="utf-8"))
    return list(data.get("package", []))


def test_pyproject_overrides_the_docstring_parser_requirement() -> None:
    """The uv override that drops anthropic's ``docstring-parser`` is present."""
    data: dict[str, Any] = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    overrides = data["tool"]["uv"]["override-dependencies"]

    assert_that(overrides).contains(_OVERRIDE)


def test_lock_pins_the_fork_and_never_installs_upstream() -> None:
    """The lockfile carries the fork, and upstream is marker-disabled everywhere."""
    packages = _lock_packages()
    names = [package["name"] for package in packages]

    assert_that(names).contains("docstring-parser-fork")

    # `docstring-parser` keeps a `[[package]]` metadata entry in the lock even
    # though nothing can install it: uv records the version it *would* have
    # resolved for a marker-disabled requirement. The enforceable invariant is
    # therefore about edges, not names — every requirement pointing at it must
    # carry the never marker, so no sync of any extra can pull it in.
    requirements = [
        dependency
        for package in packages
        for dependency in package.get("dependencies", [])
        if dependency.get("name") == "docstring-parser"
    ]
    assert_that(requirements).is_not_empty()
    for requirement in requirements:
        assert_that(requirement.get("marker")).is_equal_to("sys_platform == 'never'")


def test_pydoclint_depends_on_the_fork_only() -> None:
    """Pydoclint's locked dependency set names the fork, not upstream."""
    pydoclint = next(
        package for package in _lock_packages() if package["name"] == "pydoclint"
    )
    dependency_names = [
        dependency["name"] for dependency in pydoclint.get("dependencies", [])
    ]

    assert_that(dependency_names).contains("docstring-parser-fork")
    assert_that(dependency_names).does_not_contain("docstring-parser")


def test_ai_extra_does_not_ship_the_fork_in_wheel_metadata() -> None:
    """The published ``ai`` extra names neither ``docstring_parser`` distribution.

    The override is a uv workspace setting that pip never reads, so putting the
    fork on the extra would make ``uv pip install 'lintro[ai]'`` install two
    distributions that own the same top-level module. lintro imports neither;
    the fork belongs in the uv-only ``ai-runtime`` group instead.
    """
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    ai_extra = data["project"]["optional-dependencies"]["ai"]

    assert_that(
        any(item.startswith("docstring-parser") for item in ai_extra),
    ).is_false()


def test_ai_runtime_group_carries_the_fork() -> None:
    """The uv-only ``ai-runtime`` group restores the fork for ai-without-full syncs.

    Without it, ``uv sync --extra ai`` installs neither distribution — the
    override having stripped anthropic's — and ``anthropic.lib.tools`` raises
    ModuleNotFoundError.
    """
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    group = data["dependency-groups"]["ai-runtime"]

    assert_that(
        any(item.startswith("docstring-parser-fork") for item in group),
    ).is_true()
    assert_that(
        any(
            item.startswith("docstring-parser")
            and not item.startswith("docstring-parser-fork")
            for item in group
        ),
    ).is_false()


def test_ai_runtime_is_a_default_group() -> None:
    """``ai-runtime`` is a uv default group, so no call site has to opt in.

    Requiring `--group ai-runtime` at each `--extra ai` call site coupled those
    files to `pyproject.toml`. `ai-review.yml` runs under `pull_request_target`,
    which takes the workflow from the base branch while checking out
    `base.sha`, so a flag and a group landing in the same commit can still skew
    apart against an older base. Defaulting the group removes the coupling.
    """
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    default_groups = data["tool"]["uv"]["default-groups"]

    assert_that(default_groups).contains("ai-runtime")
    assert_that(default_groups).contains("dev")


def test_lock_records_the_fork_on_the_ai_runtime_group() -> None:
    """The locked ``ai-runtime`` group resolves the fork, unconditionally."""
    lintro = next(
        package for package in _lock_packages() if package["name"] == "lintro"
    )
    group = lintro["dev-dependencies"]["ai-runtime"]
    names = [dependency["name"] for dependency in group]

    assert_that(names).contains("docstring-parser-fork")
    assert_that(names).does_not_contain("docstring-parser")

    # A marker on this edge would resolve but never install, which is exactly
    # the failure the group exists to prevent.
    fork = next(
        dependency
        for dependency in group
        if dependency["name"] == "docstring-parser-fork"
    )
    assert_that(fork.get("marker")).is_none()
