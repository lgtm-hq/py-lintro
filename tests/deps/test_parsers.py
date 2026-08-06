"""Tests for dependency manifest parsers."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.deps.models import Dependency, Ecosystem, VersionSpecType
from lintro.deps.parsers import parse_file
from lintro.deps.parsers.cargo_parser import CargoParser
from lintro.deps.parsers.package_json_parser import PackageJsonParser
from lintro.deps.parsers.pyproject_parser import PyprojectParser
from lintro.deps.parsers.requirements_parser import RequirementsParser


def _by_name(deps: list[Dependency], name: str) -> Dependency:
    """Return the first dependency matching ``name``.

    Args:
        deps: Parsed dependencies.
        name: Package name to find.

    Returns:
        The matching dependency.
    """
    return next(d for d in deps if d.name == name)


def test_pyproject_parser_pep621(tmp_path: Path) -> None:
    """PEP 621 dependencies parse with classified specs.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                "dependencies = [",
                '  "requests>=2.28.0",',
                '  "pydantic==2.0",',
                '  "click",',
                "]",
                "[project.optional-dependencies]",
                'dev = ["pytest~=8.1.0"]',
            ],
        ),
    )
    deps = PyprojectParser().parse(manifest)
    names = {d.name for d in deps}
    assert_that(names).contains("requests", "pydantic", "click", "pytest")
    assert_that(_by_name(deps, "requests").spec_type).is_equal_to(
        VersionSpecType.UNBOUNDED,
    )
    assert_that(_by_name(deps, "pydantic").spec_type).is_equal_to(
        VersionSpecType.EXACT,
    )
    assert_that(_by_name(deps, "click").spec_type).is_equal_to(VersionSpecType.ANY)
    assert_that(_by_name(deps, "pytest").ecosystem).is_equal_to(Ecosystem.PYTHON)


def test_pyproject_parser_poetry(tmp_path: Path) -> None:
    """Poetry dependency tables parse and skip the python entry.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "\n".join(
            [
                "[tool.poetry.dependencies]",
                'python = "^3.11"',
                'requests = "^2.28.0"',
                'httpx = { version = ">=0.24,<1.0" }',
            ],
        ),
    )
    deps = PyprojectParser().parse(manifest)
    names = {d.name for d in deps}
    assert_that(names).does_not_contain("python")
    assert_that(_by_name(deps, "requests").spec_type).is_equal_to(
        VersionSpecType.CARET,
    )
    assert_that(_by_name(deps, "httpx").spec_type).is_equal_to(VersionSpecType.RANGE)


def test_requirements_parser(tmp_path: Path) -> None:
    """requirements.txt lines parse, skipping comments and options.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "requirements.txt"
    manifest.write_text(
        "\n".join(
            [
                "# a comment",
                "-r other.txt",
                "requests>=2.28.0",
                "flask==2.3.0  # inline",
                "numpy>=1.20,<2.0",
                "https://example.com/pkg.whl",
                "",
            ],
        ),
    )
    deps = RequirementsParser().parse(manifest)
    names = {d.name for d in deps}
    assert_that(names).is_equal_to({"requests", "flask", "numpy"})
    assert_that(_by_name(deps, "flask").spec_type).is_equal_to(VersionSpecType.EXACT)
    assert_that(_by_name(deps, "numpy").line).is_not_none()


def test_package_json_parser(tmp_path: Path) -> None:
    """package.json dependency maps parse across sections.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "package.json"
    manifest.write_text(
        '{"dependencies": {"react": "18.2.0", "lodash": "*", '
        '"local": "file:../x"}, "devDependencies": {"jest": "~29.0.0"}}',
    )
    deps = PackageJsonParser().parse(manifest)
    names = {d.name for d in deps}
    assert_that(names).is_equal_to({"react", "lodash", "jest"})
    assert_that(_by_name(deps, "react").spec_type).is_equal_to(VersionSpecType.EXACT)
    assert_that(_by_name(deps, "lodash").spec_type).is_equal_to(VersionSpecType.ANY)


def test_cargo_parser(tmp_path: Path) -> None:
    """Cargo.toml dependencies parse with Cargo caret semantics.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        "\n".join(
            [
                "[dependencies]",
                'serde = "1.0.100"',
                'rand = "=0.8.5"',
                'tokio = { version = "^1.20", features = ["full"] }',
            ],
        ),
    )
    deps = CargoParser().parse(manifest)
    assert_that(_by_name(deps, "serde").spec_type).is_equal_to(VersionSpecType.CARET)
    assert_that(_by_name(deps, "rand").spec_type).is_equal_to(VersionSpecType.EXACT)
    assert_that(_by_name(deps, "tokio").spec_type).is_equal_to(VersionSpecType.CARET)


def test_parse_file_dispatch_unsupported(tmp_path: Path) -> None:
    """parse_file raises on unsupported manifest names.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "go.sum"
    manifest.write_text("")
    assert_that(parse_file).raises(ValueError).when_called_with(manifest)


def test_parse_file_dispatch_requirements(tmp_path: Path) -> None:
    """parse_file routes requirements-dev.txt to the requirements parser.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "requirements-dev.txt"
    manifest.write_text("pytest==8.0.0\n")
    deps = parse_file(manifest)
    assert_that(deps).is_length(1)
    assert_that(deps[0].name).is_equal_to("pytest")


def test_pyproject_parser_poetry_legacy_dev_dependencies(tmp_path: Path) -> None:
    """Legacy ``[tool.poetry.dev-dependencies]`` tables are parsed.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "\n".join(
            [
                "[tool.poetry.dependencies]",
                'python = "^3.11"',
                'requests = "^2.28.0"',
                "[tool.poetry.dev-dependencies]",
                'pytest = "*"',
            ],
        ),
    )
    deps = PyprojectParser().parse(manifest)
    names = {d.name for d in deps}
    assert_that(names).contains("requests", "pytest")


def test_cargo_parser_target_dependencies(tmp_path: Path) -> None:
    """Platform-specific ``[target.*.dependencies]`` tables are parsed.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        "\n".join(
            [
                "[dependencies]",
                'serde = "1.0"',
                "[target.'cfg(windows)'.dependencies]",
                'winapi = "0.3"',
            ],
        ),
    )
    deps = CargoParser().parse(manifest)
    names = {d.name for d in deps}
    assert_that(names).contains("serde", "winapi")


def test_package_json_parser_keeps_git_prerelease(tmp_path: Path) -> None:
    """Semver prereleases containing ``git`` are not skipped as git URLs.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "package.json"
    manifest.write_text(
        '{"dependencies": {"demo": "1.0.0-git.1", "other": "git+https://example.com/x.git"}}',
    )
    deps = PackageJsonParser().parse(manifest)
    names = {d.name for d in deps}
    assert_that(names).contains("demo")
    assert_that(names).does_not_contain("other")
