"""Tests for dependency manifest parsers."""

from __future__ import annotations

import json
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
                "--index-url https://example.com/simple",
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


def test_package_json_parser_skips_host_shorthands(tmp_path: Path) -> None:
    """Non-registry locators are skipped instead of read as exact pins.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "dependencies": {
                    "gh": "github:owner/repo",
                    "gl": "gitlab:owner/repo",
                    "bb": "bitbucket:owner/repo",
                    "short": "owner/repo#semver:^1.0.0",
                    "linked": "link:../x",
                    "real": "^1.0.0",
                },
            },
        ),
    )
    deps = PackageJsonParser().parse(manifest)
    assert_that({d.name for d in deps}).is_equal_to({"real"})


def test_pyproject_parser_dependency_groups(tmp_path: Path) -> None:
    """PEP 735 ``[dependency-groups]`` entries are parsed.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                'dependencies = ["requests==2.31.0"]',
                "[dependency-groups]",
                'dev = ["pytest>=8.0", {include-group = "test"}]',
                'test = ["coverage~=7.4"]',
            ],
        ),
    )
    deps = PyprojectParser().parse(manifest)
    names = {d.name for d in deps}
    assert_that(names).is_equal_to({"requests", "pytest", "coverage"})
    assert_that(_by_name(deps, "pytest").spec_type).is_equal_to(
        VersionSpecType.UNBOUNDED,
    )


def test_pyproject_parser_invalid_requirement_raises(tmp_path: Path) -> None:
    """A malformed PEP 508 entry fails the parse instead of being dropped.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                'dependencies = ["requests>=", "click==1.0"]',
            ],
        ),
    )
    parser = PyprojectParser()
    assert_that(parser.parse).raises(ValueError).when_called_with(
        manifest,
    ).contains("invalid requirement")


def test_requirements_parser_invalid_line_raises(tmp_path: Path) -> None:
    """A malformed requirements line fails the parse instead of being dropped.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("requests>=2.28.0\nbroken>=\n")
    parser = RequirementsParser()
    assert_that(parser.parse).raises(ValueError).when_called_with(
        manifest,
    ).contains("line 2")


def test_cargo_parser_workspace_dependencies(tmp_path: Path) -> None:
    """``[workspace.dependencies]`` and ``workspace = true`` members parse.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        "\n".join(
            [
                "[workspace.dependencies]",
                'serde = "1.0.100"',
                'anyhow = { version = ">=1.0", features = ["std"] }',
                "[dependencies]",
                "serde = { workspace = true }",
                "anyhow = { workspace = true }",
            ],
        ),
    )
    deps = CargoParser().parse(manifest)
    assert_that({d.name for d in deps}).is_equal_to({"serde", "anyhow"})
    assert_that(_by_name(deps, "serde").spec_type).is_equal_to(VersionSpecType.CARET)
    assert_that(_by_name(deps, "anyhow").spec_type).is_equal_to(
        VersionSpecType.UNBOUNDED,
    )


def test_requirements_parser_strips_inline_hash_options(tmp_path: Path) -> None:
    """A hashed pin is parsed instead of rejected as an invalid spec.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "requirements.txt"
    manifest.write_text(
        "requests==2.31.0 --hash=sha256:abcd1234\n",
    )
    deps = RequirementsParser().parse(manifest)
    assert_that(deps).is_length(1)
    assert_that(deps[0].name).is_equal_to("requests")
    assert_that(deps[0].spec_type).is_equal_to(VersionSpecType.EXACT)


def test_requirements_parser_follows_equals_include(tmp_path: Path) -> None:
    """``--requirement=`` includes are parsed.

    Args:
        tmp_path: Temporary directory fixture.
    """
    included = tmp_path / "pins.txt"
    included.write_text("click==8.1.0\n")
    manifest = tmp_path / "requirements.txt"
    manifest.write_text(f"--requirement={included}\n")
    deps = RequirementsParser().parse(manifest)
    assert_that({d.name for d in deps}).is_equal_to({"click"})


def test_requirements_parser_skips_url_lines(tmp_path: Path) -> None:
    """PEP 508 URL locators in requirements files are skipped.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "requirements.txt"
    manifest.write_text(
        "requests==2.31.0\ndemo @ git+https://example.com/demo.git\n",
    )
    deps = RequirementsParser().parse(manifest)
    assert_that({d.name for d in deps}).is_equal_to({"requests"})


def test_requirements_parser_breaks_include_cycles(tmp_path: Path) -> None:
    """Cyclic ``-r`` includes do not recurse forever.

    Args:
        tmp_path: Temporary directory fixture.
    """
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("-r b.txt\nflask==2.3.0\n")
    second.write_text("-r a.txt\n")
    deps = RequirementsParser().parse(first)
    assert_that({d.name for d in deps}).is_equal_to({"flask"})


def test_requirements_parser_follows_includes(tmp_path: Path) -> None:
    """``-r`` includes are parsed relative to the referencing file.

    Args:
        tmp_path: Temporary directory fixture.
    """
    included = tmp_path / "base.txt"
    included.write_text("flask==2.3.0\n")
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("-r base.txt\nrequests==2.31.0\n")
    deps = RequirementsParser().parse(manifest)
    assert_that({d.name for d in deps}).is_equal_to({"flask", "requests"})


def test_requirements_parser_missing_include_raises(tmp_path: Path) -> None:
    """A missing ``-r`` include fails closed.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("-r missing.txt\n")
    parser = RequirementsParser()
    assert_that(parser.parse).raises(ValueError).when_called_with(
        manifest,
    ).contains("not found")


def test_parse_file_dev_requirements_and_requirements_dir(
    tmp_path: Path,
) -> None:
    """parse_file accepts ``*requirements.txt`` and ``requirements/*.txt``.

    Args:
        tmp_path: Temporary directory fixture.
    """
    dev = tmp_path / "dev-requirements.txt"
    dev.write_text("pytest==8.0.0\n")
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    nested = req_dir / "base.txt"
    nested.write_text("click==8.1.0\n")
    assert_that(parse_file(dev)[0].name).is_equal_to("pytest")
    assert_that(parse_file(nested)[0].name).is_equal_to("click")


def test_pyproject_parser_skips_url_requirements(tmp_path: Path) -> None:
    """PEP 508 URL/VCS locators are skipped instead of flagged as unbounded.

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
                '  "requests==2.31.0",',
                '  "demo @ git+https://example.com/demo.git",',
                "]",
            ],
        ),
    )
    deps = PyprojectParser().parse(manifest)
    assert_that({d.name for d in deps}).is_equal_to({"requests"})


def test_cargo_parser_resolves_parent_workspace_member(tmp_path: Path) -> None:
    """A member ``{ workspace = true }`` reads the parent workspace table.

    Args:
        tmp_path: Temporary directory fixture.
    """
    root = tmp_path / "Cargo.toml"
    root.write_text(
        "\n".join(
            [
                "[workspace]",
                'members = ["member"]',
                "[workspace.dependencies]",
                'serde = "1.0.100"',
            ],
        ),
    )
    member_dir = tmp_path / "member"
    member_dir.mkdir()
    member = member_dir / "Cargo.toml"
    member.write_text(
        "\n".join(
            [
                "[dependencies]",
                "serde = { workspace = true }",
                'rand = "0.8"',
            ],
        ),
    )
    deps = CargoParser().parse(member)
    assert_that({d.name for d in deps}).is_equal_to({"serde", "rand"})
    assert_that(_by_name(deps, "serde").spec_type).is_equal_to(
        VersionSpecType.CARET,
    )


def test_cargo_parser_skips_unresolvable_workspace_member(tmp_path: Path) -> None:
    """A ``workspace = true`` entry with no workspace table is skipped.

    Args:
        tmp_path: Temporary directory fixture.
    """
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        "\n".join(
            [
                "[dependencies]",
                "serde = { workspace = true }",
                'rand = "0.8"',
            ],
        ),
    )
    deps = CargoParser().parse(manifest)
    assert_that({d.name for d in deps}).is_equal_to({"rand"})
