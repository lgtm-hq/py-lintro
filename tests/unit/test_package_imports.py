"""Smoke tests to verify all package modules are importable.

This test ensures that:
1. All modules listed in pyproject.toml are actually included in the package build
2. All packages in the source tree are listed in pyproject.toml (catches forgotten packages)

This prevents packaging errors where a module exists in the source tree but is
missing from the packages list (like the 0.43.0 bug with lintro.utils.environment).
"""

import importlib
from pathlib import Path

import pytest

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


def _get_packages_from_pyproject() -> set[str]:
    """Resolve the packages selected by pyproject.toml packaging config.

    Evaluates the [tool.setuptools.packages.find] directive with the same
    where/include/exclude settings setuptools uses at build time.

    Returns:
        Set of package names resolved from the find directive.
    """
    import tomllib
    from fnmatch import fnmatchcase

    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    find_config = (
        data.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
    )
    where_dirs = find_config.get("where", ["."])
    include = find_config.get("include", ["*"])
    exclude = find_config.get("exclude", [])

    packages: set[str] = set()
    for where in where_dirs:
        base = (PROJECT_ROOT / where).resolve()
        for init_file in base.rglob("__init__.py"):
            parts = init_file.parent.relative_to(base).parts
            if not parts:
                continue
            # Mirror setuptools find_packages: every ancestor directory
            # must itself be a package (contain __init__.py).
            ancestors_are_packages = all(
                (base / Path(*parts[: depth + 1]) / "__init__.py").is_file()
                for depth in range(len(parts))
            )
            if not ancestors_are_packages:
                continue
            package_name = ".".join(parts)
            if not any(fnmatchcase(package_name, pat) for pat in include):
                continue
            if any(fnmatchcase(package_name, pat) for pat in exclude):
                continue
            packages.add(package_name)
    return packages


def _get_configured_packages() -> list[str]:
    """Get packages from pyproject.toml for parametrized tests."""
    return sorted(_get_packages_from_pyproject())


@pytest.mark.parametrize("package", _get_configured_packages())
def test_package_importable(package: str) -> None:
    """Verify each configured package can be imported successfully."""
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
            f"This likely means the package is not selected by "
            f"[tool.setuptools.packages.find] in pyproject.toml",
        )


def test_all_source_packages_are_configured() -> None:
    """Verify all packages in the source tree are listed in pyproject.toml.

    This catches the case where a new package directory is not picked up
    by the [tool.setuptools.packages.find] directive (e.g. because it is
    matched by an exclude pattern or lacks an __init__.py).
    """
    source_packages = _discover_packages_from_source()
    configured_packages = _get_packages_from_pyproject()

    missing = source_packages - configured_packages
    if missing:
        missing_list = "\n  - ".join(sorted(missing))
        pytest.fail(
            f"Found {len(missing)} package(s) in source tree not selected by "
            f"pyproject.toml [tool.setuptools.packages.find]:\n  - {missing_list}\n\n"
            f"Adjust the find directive so these packages ship in the build.",
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
