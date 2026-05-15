"""Tests for ``_generator.seed.parse_seed``."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that


def test_parse_seed_happy_path(gen: ModuleType, fake_repo: Path) -> None:
    """Seed parsing extracts npm and pypi owner mappings.

    Args:
        gen: Imported generator module.
        fake_repo: Fake repo fixture.
    """
    seed = gen.parse_seed(fake_repo / "lintro" / "_tool_packages.py")
    assert_that(seed.npm_owners).is_equal_to(
        {"oxfmt": "OXFMT", "@astrojs/check": None},
    )
    assert_that(seed.pypi_owners).is_equal_to({"pytest": "PYTEST"})


def test_parse_seed_missing_file_errors(gen: ModuleType, tmp_path: Path) -> None:
    """Parsing a missing seed raises GenerationError.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    with pytest.raises(gen.GenerationError, match="seed file not found"):
        gen.parse_seed(tmp_path / "nope.py")


def test_parse_seed_invalid_python_errors(gen: ModuleType, tmp_path: Path) -> None:
    """Malformed seed Python raises GenerationError.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    bad = tmp_path / "seed.py"
    bad.write_text("NPM_PACKAGE_OWNERS = {\n")

    with pytest.raises(gen.GenerationError, match="not valid Python"):
        gen.parse_seed(bad)


def test_parse_seed_rejects_non_toolname_value(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """Values must be ``ToolName.X`` or ``None`` literals.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    bad = tmp_path / "seed.py"
    bad.write_text(
        'NPM_PACKAGE_OWNERS: dict[str, object] = {"x": "not a toolname"}\n'
        "PYPI_PACKAGE_OWNERS: dict[str, object] = {}\n",
    )
    with pytest.raises(gen.GenerationError, match="ToolName"):
        gen.parse_seed(bad)
