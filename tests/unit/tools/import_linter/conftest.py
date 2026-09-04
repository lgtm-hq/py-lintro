"""Shared fixtures for the import-linter plugin unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lintro.tools.definitions.import_linter import ImportLinterPlugin

BROKEN_OUTPUT = """\
---------
Contracts
---------

Analyzed 6 files, 5 dependencies.
---------------------------------

Layered architecture BROKEN

Contracts: 0 kept, 1 broken.


----------------
Broken contracts
----------------

Layered architecture
--------------------

layered.storage is not allowed to import layered.api:

- layered.storage -> layered.helpers (l.6)
  layered.helpers -> layered.compat (l.3)
  layered.compat -> layered.api (l.3)

"""

KEPT_OUTPUT = """\
---------
Contracts
---------

Analyzed 4 files, 2 dependencies.
---------------------------------

Layered architecture KEPT

Contracts: 1 kept, 0 broken.
"""


@pytest.fixture
def import_linter_plugin() -> ImportLinterPlugin:
    """Provide a fresh import-linter plugin instance.

    Returns:
        An ``ImportLinterPlugin`` instance.
    """
    return ImportLinterPlugin()


@pytest.fixture
def broken_output() -> str:
    """Return ``lint-imports`` output with one broken contract.

    Returns:
        Raw tool output.
    """
    return BROKEN_OUTPUT


@pytest.fixture
def kept_output() -> str:
    """Return ``lint-imports`` output where every contract is kept.

    Returns:
        Raw tool output.
    """
    return KEPT_OUTPUT


@pytest.fixture
def project_with_contracts(tmp_path: Path) -> Path:
    """Create a project directory whose pyproject declares import contracts.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the project root.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.importlinter]\nroot_package = "pkg"\n',
        encoding="utf-8",
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


@pytest.fixture
def project_without_contracts(tmp_path: Path) -> Path:
    """Create a project directory with no import-linter configuration.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the project root.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\n',
        encoding="utf-8",
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path
