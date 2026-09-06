"""Smoke tests to verify all package modules are importable.

This test ensures that:
1. Every package directory in the source tree imports cleanly
2. The build discovers packages automatically, so no package can be forgotten

The wheel is built with ``[tool.setuptools.packages.find]`` (#1225), so packages
are discovered by walking the tree for ``__init__.py`` rather than read from a
hand-maintained list. That directive is what prevents a repeat of the 0.43.0
packaging bug, where ``lintro.utils.environment`` was missing from the wheel.
"""

import importlib
from pathlib import Path

import pytest
from assertpy import assert_that

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent


def _discover_packages_from_source() -> set[str]:
    """Discover all Python packages in the lintro source tree.

    Returns:
        Set of package names (e.g., "lintro.utils.environment").
    """
    lintro_dir = PROJECT_ROOT / "lintro"
    packages: set[str] = set()

    for path in lintro_dir.rglob("__init__.py"):
        # Convert path to package name
        relative = path.parent.relative_to(PROJECT_ROOT)
        package_name = ".".join(relative.parts)
        packages.add(package_name)

    return packages


def _get_find_directive() -> dict[str, object]:
    """Read the setuptools package-discovery directive from pyproject.toml.

    Returns:
        The ``[tool.setuptools.packages.find]`` table.
    """
    import tomllib

    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    find: dict[str, object] = data["tool"]["setuptools"]["packages"]["find"]
    return find


@pytest.mark.parametrize("package", sorted(_discover_packages_from_source()))
def test_package_importable(package: str) -> None:
    """Verify each source-tree package imports cleanly.

    This imports from the checkout, so it proves the module is importable at
    all, not that it reaches the wheel. Distribution membership is asserted by
    ``tests/integration/test_built_package.py``, which inspects the built
    archives.

    Args:
        package: Dotted package name discovered in the source tree.
    """
    # Note: We intentionally don't clear sys.modules here because doing so
    # would reinitialize global singletons (like tool_manager in lintro.tools)
    # which breaks other tests that depend on monkeypatching those singletons.
    # The import test is still valid - if the package is missing from
    # pyproject.toml, it won't be importable in a fresh install.
    try:
        # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
        importlib.import_module(package)
    except ImportError as e:
        pytest.fail(
            f"Failed to import '{package}': {e}\n"
            f"The package is broken in the source tree; whether it ships is "
            f"checked by tests/integration/test_built_package.py",
        )


def test_package_discovery_covers_every_source_package() -> None:
    """Verify the find directive includes every source-tree package.

    A developer adding a package directory must not have to edit
    pyproject.toml, so the directive has to match the whole ``lintro`` tree
    while excluding the repo-only trees that ``lintro*`` would otherwise pull
    in (notably the in-tree build backend, ``lintro_build``).

    This is a fast configuration guard: it reads the declared directive rather
    than a built artifact. What setuptools actually puts in the wheel and the
    sdist is asserted by the slow
    ``tests/integration/test_built_package.py`` distribution tests.
    """
    find = _get_find_directive()

    assert_that(find["include"]).is_equal_to(["lintro*"])
    assert_that(find["namespaces"]).is_false()
    assert_that(find["exclude"]).contains(
        "lintro_build*",
        "tests*",
        "test_samples*",
        "scripts*",
        "docs*",
        "evals*",
    )
    assert_that(sorted(_discover_packages_from_source())).contains(
        "lintro",
        "lintro.utils.environment",
    )


def test_doctor_command_imports() -> None:
    """Verify the doctor command and its dependencies are importable.

    This is a regression test for the 0.43.0 packaging bug where
    lintro.utils.environment was missing from the package.
    """
    from lintro.cli_utils.commands import doctor  # noqa: F401
    from lintro.utils.environment import (  # noqa: F401
        CIEnvironment,
        EnvironmentReport,
        GoInfo,
        LintroInfo,
        NodeInfo,
        ProjectInfo,
        PythonInfo,
        RubyInfo,
        RustInfo,
        SystemInfo,
        collect_full_environment,
        render_environment_report,
    )
