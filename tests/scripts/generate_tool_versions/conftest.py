"""Shared fixtures for tool-version generator tests.

Loads the hyphen-named entry script via ``importlib.spec_from_file_location``
(also bootstrapping ``sys.path`` so its sibling ``_generator`` package
becomes importable), and exposes a fake-repo fixture plus a
``retargeted_gen`` fixture for end-to-end ``main()`` tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Generator
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "generate-tool-versions.py"


def _load_generator_module(module_name: str) -> ModuleType:
    """Import the generator entry script under ``module_name``.

    Args:
        module_name: Name used to register the loaded module in ``sys.modules``.

    Returns:
        Imported module exposing top-level helpers and path constants. Private
        package helpers exercised by tests are attached as module attributes for
        ergonomic access.
    """
    spec = importlib.util.spec_from_file_location(
        module_name,
        SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load generator script at {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so frozen-dataclass machinery
    # (sys.modules.get(cls.__module__).__dict__) resolves cleanly.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    # Expose private package helpers needed by tests.
    from _generator.inputs import _collect_dep_strings  # noqa: PLC0415

    module._collect_dep_strings = _collect_dep_strings  # type: ignore[attr-defined]
    return module


@pytest.fixture(scope="session")
def gen() -> ModuleType:
    """Import the generator entry script as a module.

    Returns:
        Imported module exposing top-level helpers and path constants.
    """
    return _load_generator_module("generate_tool_versions")


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
def retargeted_gen(fake_repo: Path) -> ModuleType:
    """Fresh generator module with its module-level paths pointed at ``fake_repo``.

    The hyphen-named import means mypy cannot statically resolve these
    attributes, hence the per-line type-ignore.

    Args:
        fake_repo: Fake repo fixture root.

    Returns:
        A generator module instance with paths bound to the fake repo.
    """
    module = _load_generator_module(f"generate_tool_versions_{id(fake_repo)}")
    module.REPO_ROOT = fake_repo  # type: ignore[attr-defined]
    module.SEED_PATH = fake_repo / "lintro" / "_tool_packages.py"  # type: ignore[attr-defined]
    module.TOOL_VERSIONS_PATH = fake_repo / "lintro" / "_tool_versions.py"  # type: ignore[attr-defined]
    module.PACKAGE_JSON_PATH = fake_repo / "package.json"  # type: ignore[attr-defined]
    module.PYPROJECT_PATH = fake_repo / "pyproject.toml"  # type: ignore[attr-defined]
    module.MANIFEST_PATH = fake_repo / "lintro" / "tools" / "manifest.json"  # type: ignore[attr-defined]
    module.GENERATED_PATH = fake_repo / "lintro" / "_generated_versions.py"  # type: ignore[attr-defined]
    return module
