"""Detection of pytest coverage flags declared in project configuration.

pytest injects ``addopts`` from a project's configuration files (``pytest.ini``,
``pyproject.toml``, ``setup.cfg``, ``tox.ini``) before any command lintro builds
runs. When those ``addopts`` enable coverage (``--cov`` flags) but lintro's own
coverage options are unset, lintro's run banner would report coverage as
``disabled`` while coverage actually runs. This module detects coverage flags in
those configuration files so the banner can reflect reality.
"""

from __future__ import annotations

import configparser
import tomllib
from pathlib import Path

# Coverage marker recognised in pytest addopts. ``--cov`` covers ``--cov``,
# ``--cov=pkg``, ``--cov-report=...`` and every other pytest-cov flag.
_COV_MARKER: str = "--cov"

# Config files scanned, in pytest's own precedence order.
_INI_FILES: tuple[str, ...] = ("pytest.ini", "tox.ini", "setup.cfg")


def _addopts_has_coverage(addopts: str | list[str] | None) -> bool:
    """Return whether an ``addopts`` value enables coverage.

    Args:
        addopts: Raw ``addopts`` value (string or list of tokens) or None.

    Returns:
        bool: True if a ``--cov`` flag is present, False otherwise.
    """
    if not addopts:
        return False
    if isinstance(addopts, list):
        return any(_COV_MARKER in str(token) for token in addopts)
    return _COV_MARKER in str(addopts)


def _ini_addopts_has_coverage(path: Path, section: str) -> bool:
    """Return whether an INI-style config's section enables coverage.

    Args:
        path: Path to the INI/CFG file to read.
        section: Section name holding pytest options (e.g. ``pytest``).

    Returns:
        bool: True if the section's ``addopts`` enables coverage.
    """
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return False
    if not parser.has_section(section):
        return False
    return _addopts_has_coverage(parser.get(section, "addopts", fallback=None))


def _pyproject_addopts_has_coverage(path: Path) -> bool:
    """Return whether ``pyproject.toml`` pytest config enables coverage.

    Args:
        path: Path to the ``pyproject.toml`` file to read.

    Returns:
        bool: True if ``[tool.pytest.ini_options].addopts`` enables coverage.
    """
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError):
        return False
    ini_options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    return _addopts_has_coverage(ini_options.get("addopts"))


def config_addopts_enable_coverage(root: str | Path | None = None) -> bool:
    """Detect whether pytest configuration ``addopts`` enable coverage.

    Scans the project's pytest configuration files for ``--cov`` flags declared
    in ``addopts``. This lets lintro report coverage accurately even when the
    flags come from configuration rather than lintro's own options.

    Args:
        root: Directory to search for configuration files. Defaults to the
            current working directory.

    Returns:
        bool: True if any pytest configuration enables coverage via ``addopts``.
    """
    base = Path(root) if root is not None else Path.cwd()

    # INI/CFG files use different section names for pytest options.
    section_by_file: dict[str, str] = {
        "pytest.ini": "pytest",
        "tox.ini": "pytest",
        "setup.cfg": "tool:pytest",
    }
    for filename in _INI_FILES:
        path = base / filename
        if path.is_file() and _ini_addopts_has_coverage(
            path,
            section_by_file[filename],
        ):
            return True

    pyproject = base / "pyproject.toml"
    return pyproject.is_file() and _pyproject_addopts_has_coverage(pyproject)
