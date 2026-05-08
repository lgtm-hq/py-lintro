"""Tests for ``_generator.inputs`` readers and validators."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_NON_EXACT_NPM_SPECS = [
    ">=1.0.0",
    "*",
    "latest",
    "git+https://example.com/foo.git",
    "file:../foo",
    "workspace:*",
    "npm:foo@1.0.0",
    "1.x",
]

_EXACT_NPM_SPECS = [
    "1.2.3",
    "^1.2.3",
    "~1.2.3",
    "1.2.3-rc.1",
    "1.2.3+build.5",
]


def test_read_package_json_strips_caret(gen: ModuleType, fake_repo: Path) -> None:
    """Caret/tilde prefixes are stripped from version specifiers.

    Args:
        gen: Imported generator module.
        fake_repo: Fake repo fixture.
    """
    versions = gen.read_package_json(fake_repo / "package.json")
    assert_that(versions["oxfmt"]).is_equal_to("0.43.0")
    assert_that(versions["@astrojs/check"]).is_equal_to("0.9.8")


@pytest.mark.parametrize("spec", _NON_EXACT_NPM_SPECS)
def test_read_package_json_strict_rejects_non_exact(
    gen: ModuleType,
    tmp_path: Path,
    spec: str,
) -> None:
    """Seeded packages with non-exact specs raise GenerationError.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
        spec: Offending version spec.
    """
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"devDependencies": {"oxfmt": spec}}))
    with pytest.raises(gen.GenerationError, match="oxfmt"):
        gen.read_package_json(pkg, strict_packages={"oxfmt"})


@pytest.mark.parametrize("spec", _EXACT_NPM_SPECS)
def test_read_package_json_strict_accepts_exact(
    gen: ModuleType,
    tmp_path: Path,
    spec: str,
) -> None:
    """Exact SemVer pins (with optional ^/~ prefix) pass strict validation.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
        spec: Acceptable version spec.
    """
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"devDependencies": {"oxfmt": spec}}))
    versions = gen.read_package_json(pkg, strict_packages={"oxfmt"})
    assert_that(versions["oxfmt"]).is_equal_to(spec.lstrip("^~"))


def test_read_package_json_non_strict_passes_through(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """Packages outside the strict set are not version-validated.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    pkg = tmp_path / "package.json"
    pkg.write_text(
        json.dumps(
            {
                "devDependencies": {
                    "oxfmt": "0.43.0",
                    "some-other-dep": ">=1.0.0",
                },
            },
        ),
    )
    versions = gen.read_package_json(pkg, strict_packages={"oxfmt"})
    assert_that(versions["oxfmt"]).is_equal_to("0.43.0")
    assert_that(versions["some-other-dep"]).is_equal_to(">=1.0.0")


def test_read_pyproject_versions_dedupes(gen: ModuleType, tmp_path: Path) -> None:
    """Same package pinned identically across tables yields one version.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    pyp = tmp_path / "pyproject.toml"
    pyp.write_text(
        '[project]\ndependencies = ["pytest>=9.0.3"]\n'
        '[project.optional-dependencies]\ndev = ["pytest>=9.0.3"]\n',
    )
    versions = gen.read_pyproject_versions(pyp, {"pytest"})
    assert_that(versions).is_equal_to({"pytest": "9.0.3"})


def test_read_pyproject_versions_inconsistent_raises(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """Conflicting version pins for the same package fail generation.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    pyp = tmp_path / "pyproject.toml"
    pyp.write_text(
        '[project]\ndependencies = ["pytest>=9.0.3"]\n'
        '[dependency-groups]\ntest = ["pytest>=8.0.0"]\n',
    )
    with pytest.raises(gen.GenerationError, match="inconsistent"):
        gen.read_pyproject_versions(pyp, {"pytest"})


def test_read_pyproject_versions_missing_raises(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """A seeded package with no pin in pyproject.toml fails generation.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    pyp = tmp_path / "pyproject.toml"
    pyp.write_text("[project]\ndependencies = []\n")
    with pytest.raises(gen.GenerationError, match="not found with a version pin"):
        gen.read_pyproject_versions(pyp, {"pytest"})


def test_collect_dep_strings_skips_non_dep_tables(gen: ModuleType) -> None:
    """Strings outside known dep tables are ignored.

    Args:
        gen: Imported generator module.
    """
    data = {
        "project": {
            "dependencies": ["pytest>=9.0.3"],
            "optional-dependencies": {"dev": ["mypy>=1.19.1"]},
            "keywords": ["lint", "format"],
        },
        "dependency-groups": {"test": ["ruff>=0.15.9"]},
        "tool": {
            "uv": {
                "constraint-dependencies": ["semgrep>=1.151.0"],
                "override-dependencies": ["sqlfluff>=4.0.0"],
                "sources": {"foo": {"git": "https://example.com/foo"}},
            },
            "lintro": {"banner": "looks-like-a-package>=1.0.0"},
        },
    }
    found = sorted(gen._collect_dep_strings(data))
    assert_that(found).is_equal_to(
        [
            "mypy>=1.19.1",
            "pytest>=9.0.3",
            "ruff>=0.15.9",
            "semgrep>=1.151.0",
            "sqlfluff>=4.0.0",
        ],
    )


def test_collect_dep_strings_skips_pep735_include_group(gen: ModuleType) -> None:
    """PEP 735 ``include-group`` dict entries are ignored.

    Args:
        gen: Imported generator module.
    """
    data = {
        "dependency-groups": {
            "test": ["pytest>=9.0.3", {"include-group": "dev"}],
            "dev": ["ruff>=0.15.9"],
        },
    }
    found = sorted(gen._collect_dep_strings(data))
    assert_that(found).is_equal_to(["pytest>=9.0.3", "ruff>=0.15.9"])


def test_read_binary_tool_versions(gen: ModuleType, tmp_path: Path) -> None:
    """Reads a flat ``ToolName.X: "ver"`` mapping from TOOL_VERSIONS.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    tv = tmp_path / "_tool_versions.py"
    tv.write_text(
        "from lintro.enums.tool_name import ToolName\n"
        "TOOL_VERSIONS: dict = {\n"
        '    ToolName.HADOLINT: "2.14.0",\n'
        '    ToolName.RUSTFMT: "1.8.0",\n'
        "}\n"
        "OTHER_DICT = {\n"
        '    ToolName.IGNORED: "9.9.9",\n'
        "}\n",
    )
    versions = gen.read_binary_tool_versions(tv)
    assert_that(versions).is_equal_to({"hadolint": "2.14.0", "rustfmt": "1.8.0"})
