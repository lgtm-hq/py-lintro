"""Shared fixtures for tool-version generator tests.

The generator implementation lives in the importable ``lintro_build.versions``
package; the ``gen`` fixture exposes it, and ``retargeted_gen`` binds its
``main`` entry point to a fake-repo ``GeneratorPaths`` for end-to-end tests.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import lintro_build.versions as versions_package
from lintro_build.versions.generate import main as generate_main
from lintro_build.versions.paths import GeneratorPaths

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "generate-tool-versions.py"


@pytest.fixture(scope="session")
def gen() -> ModuleType:
    """Expose the generator package for unit tests.

    Returns:
        The ``lintro_build.versions`` package.
    """
    return versions_package


@pytest.fixture
def fake_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a minimal fake repo with seed, package.json, pyproject, manifest.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        Path: Path to the fake repo root.
    """
    (tmp_path / "lintro").mkdir()
    (tmp_path / "lintro" / "tools").mkdir()
    (tmp_path / "scripts" / "ci").mkdir(parents=True)

    (tmp_path / "lintro" / "_tool_packages.py").write_text(
        "from lintro.enums.tool_name import ToolName\n"
        "NPM_PACKAGE_OWNERS: dict[str, ToolName | None] = {\n"
        '    "oxfmt": ToolName.OXFMT,\n'
        '    "@astrojs/check": None,\n'
        "}\n"
        "PYPI_PACKAGE_OWNERS: dict[str, ToolName | None] = {\n"
        '    "pytest": ToolName.PYTEST,\n'
        "}\n",
    )

    (tmp_path / "lintro" / "_tool_versions.py").write_text(
        "from lintro.enums.tool_name import ToolName\n"
        "TOOL_VERSIONS: dict = {\n"
        "}\n",
    )

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {
                    "oxfmt": "^0.43.0",
                    "@astrojs/check": "0.9.8",
                },
            },
            indent=2,
        ),
    )

    (tmp_path / "pyproject.toml").write_text(
        """[project]
name = "fake"
dependencies = ["pytest>=9.0.3"]
""",
    )

    (tmp_path / "lintro" / "tools" / "manifest.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "oxfmt",
                        "version": "0.0.0",
                        "install": {"type": "npm", "package": "oxfmt"},
                    },
                    {
                        "name": "pytest",
                        "version": "0.0.0",
                        "install": {"type": "pip", "package": "pytest"},
                    },
                ],
            },
            indent=2,
        )
        + "\n",
    )

    yield tmp_path


@pytest.fixture
def retargeted_gen(fake_repo: Path) -> SimpleNamespace:
    """Generator entry point bound to ``GeneratorPaths`` for the fake repo.

    Args:
        fake_repo: Fake repo fixture root.

    Returns:
        Namespace exposing ``main`` (paths pre-bound), the bound ``paths``,
        and the exit-code constants.
    """
    paths = GeneratorPaths.from_repo_root(fake_repo)

    def _main(argv: list[str] | None = None) -> int:
        """Run the generator against the fake repo.

        Args:
            argv: CLI arguments to pass through.

        Returns:
            Process exit code.
        """
        return generate_main(argv, paths=paths)

    return SimpleNamespace(
        main=_main,
        paths=paths,
        EXIT_OK=versions_package.EXIT_OK,
        EXIT_DRIFT=versions_package.EXIT_DRIFT,
        EXIT_INPUT_ERROR=versions_package.EXIT_INPUT_ERROR,
    )
