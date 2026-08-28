"""Tests for the ``lintro_build.generate_all`` aggregation API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

import lintro_build


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Create a fake repo with inputs for both generators.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path to the fake repo root.
    """
    (tmp_path / "lintro" / "tools" / "definitions").mkdir(parents=True)
    (tmp_path / "lintro" / "plugins").mkdir()

    (tmp_path / "lintro" / "_tool_packages.py").write_text(
        "from lintro.enums.tool_name import ToolName\n"
        "NPM_PACKAGE_OWNERS: dict[str, ToolName | None] = {\n"
        '    "oxfmt": ToolName.OXFMT,\n'
        "}\n"
        "PYPI_PACKAGE_OWNERS: dict[str, ToolName | None] = {\n"
        "}\n",
    )
    (tmp_path / "lintro" / "_tool_versions.py").write_text(
        "from lintro.enums.tool_name import ToolName\n"
        "TOOL_VERSIONS: dict = {\n"
        "}\n",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"oxfmt": "^0.43.0"}}, indent=2),
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fake"\n')
    (tmp_path / "lintro" / "tools" / "manifest.src.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "oxfmt",
                        "install": {"type": "npm", "package": "oxfmt"},
                    },
                ],
            },
            indent=2,
        )
        + "\n",
    )
    (tmp_path / "lintro" / "tools" / "definitions" / "oxfmt.py").write_text(
        "@register_tool\nclass Plugin:\n    pass\n",
    )
    return tmp_path


def test_generate_all_write_then_check_is_clean(fake_repo: Path) -> None:
    """Write mode produces outputs that pass a subsequent check.

    Args:
        fake_repo: Fake repo fixture root.
    """
    assert_that(lintro_build.generate_all(fake_repo)).is_equal_to(
        lintro_build.EXIT_OK,
    )
    assert_that(
        (fake_repo / "lintro" / "_generated_versions.py").exists(),
    ).is_true()
    assert_that(
        (fake_repo / "lintro" / "plugins" / "_builtin_index.py").exists(),
    ).is_true()
    assert_that(lintro_build.generate_all(fake_repo, check=True)).is_equal_to(
        lintro_build.EXIT_OK,
    )


def test_generate_all_reports_version_drift(fake_repo: Path) -> None:
    """A stale version artifact surfaces as drift in check mode.

    Args:
        fake_repo: Fake repo fixture root.
    """
    lintro_build.generate_all(fake_repo)

    pkg = fake_repo / "package.json"
    data = json.loads(pkg.read_text())
    data["devDependencies"]["oxfmt"] = "0.99.0"
    pkg.write_text(json.dumps(data, indent=2))

    assert_that(lintro_build.generate_all(fake_repo, check=True)).is_equal_to(
        lintro_build.EXIT_DRIFT,
    )


def test_generate_all_reports_index_drift(fake_repo: Path) -> None:
    """A stale builtin index surfaces as drift even when versions are clean.

    Args:
        fake_repo: Fake repo fixture root.
    """
    lintro_build.generate_all(fake_repo)

    (fake_repo / "lintro" / "tools" / "definitions" / "extra.py").write_text(
        "HELPER = True\n",
    )

    assert_that(lintro_build.generate_all(fake_repo, check=True)).is_equal_to(
        lintro_build.EXIT_DRIFT,
    )


def test_generate_all_input_error_beats_drift(
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An input error dominates drift, and both generators still run.

    Args:
        fake_repo: Fake repo fixture root.
        capsys: Pytest stdout/stderr capture.
    """
    lintro_build.generate_all(fake_repo)

    (fake_repo / "package.json").unlink()
    (fake_repo / "lintro" / "tools" / "definitions" / "extra.py").write_text(
        "HELPER = True\n",
    )

    assert_that(lintro_build.generate_all(fake_repo, check=True)).is_equal_to(
        lintro_build.EXIT_INPUT_ERROR,
    )
    captured = capsys.readouterr()
    assert_that(captured.err).contains("package.json")
    assert_that(captured.out).contains("out of date")
