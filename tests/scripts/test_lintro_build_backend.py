"""Tests for the in-tree PEP 517 backend's artifact-ensuring logic (#2180)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro_build import backend


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Create a fake repo with every generator input present.

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
    (tmp_path / "requirements-semgrep.txt").write_text("")
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


def test_ensure_artifacts_generates_when_inputs_present(fake_repo: Path) -> None:
    """The inputs-present branch generates all three artifacts.

    Args:
        fake_repo: Fake repo fixture root.
    """
    backend._ensure_artifacts(fake_repo)

    for path in backend._artifact_paths(fake_repo):
        assert_that(path.exists()).described_as(str(path)).is_true()


def test_ensure_artifacts_trusts_baked_outputs_without_inputs(
    fake_repo: Path,
) -> None:
    """Outputs present + inputs absent (sdist tree) skips generation.

    Args:
        fake_repo: Fake repo fixture root.
    """
    backend._ensure_artifacts(fake_repo)
    baked = {path: path.read_text() for path in backend._artifact_paths(fake_repo)}

    (fake_repo / "package.json").unlink()
    (fake_repo / "requirements-semgrep.txt").unlink()

    backend._ensure_artifacts(fake_repo)
    for path, before in baked.items():
        assert_that(path.read_text()).described_as(str(path)).is_equal_to(before)


def test_ensure_artifacts_fails_without_inputs_or_outputs(
    fake_repo: Path,
) -> None:
    """Neither inputs nor outputs is a loud, actionable failure.

    Args:
        fake_repo: Fake repo fixture root.
    """
    (fake_repo / "package.json").unlink()

    with pytest.raises(backend.BuildInputsMissingError, match="package.json"):
        backend._ensure_artifacts(fake_repo)


def test_ensure_artifacts_surfaces_generation_failure(fake_repo: Path) -> None:
    """A generator input error becomes a build failure, not a silent skip.

    Args:
        fake_repo: Fake repo fixture root.
    """
    (fake_repo / "lintro" / "tools" / "manifest.src.json").unlink()

    with pytest.raises(backend.BuildInputsMissingError, match="exit code 2"):
        backend._ensure_artifacts(fake_repo)
