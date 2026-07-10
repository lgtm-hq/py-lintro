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

# Config files scanned, in pytest's own precedence order.
_INI_FILES: tuple[str, ...] = ("pytest.ini", "tox.ini", "setup.cfg")


def _token_enables_coverage(token: str) -> bool:
    """Return whether a single addopts token selects a coverage source.

    Report/config-only flags such as ``--cov-report`` / ``--cov-config`` /
    ``--no-cov-on-fail`` do not enable coverage by themselves.

    Args:
        token: One whitespace-delimited addopts token.

    Returns:
        bool: True if the token is ``--cov`` or ``--cov=<path>``.
    """
    return token == "--cov" or token.startswith("--cov=")


def _addopts_has_coverage(addopts: str | list[str] | None) -> bool:
    """Return whether an ``addopts`` value enables coverage.

    Args:
        addopts: Raw ``addopts`` value (string or list of tokens) or None.

    Returns:
        bool: True if a coverage-enabling ``--cov`` / ``--cov=`` flag is present.
    """
    if not addopts:
        return False
    tokens: list[str]
    if isinstance(addopts, list):
        tokens = [str(token) for token in addopts]
    else:
        tokens = str(addopts).split()
    return any(_token_enables_coverage(token) for token in tokens)


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


def _directory_has_pytest_config(base: Path) -> bool:
    """Return whether ``base`` contains a pytest configuration file.

    Pytest stops at the first matching config while walking upward, so
    ancestor configs beyond that directory are ignored.

    Args:
        base: Directory that may contain pytest configuration files.

    Returns:
        bool: True if ``base`` has pytest.ini, tox.ini, setup.cfg, or
            pyproject.toml (presence only; content is not inspected).
    """
    if any((base / filename).is_file() for filename in _INI_FILES):
        return True
    return (base / "pyproject.toml").is_file()


def _directory_enables_coverage(base: Path) -> bool:
    """Return whether pytest config in ``base`` enables coverage.

    Args:
        base: Directory that may contain pytest configuration files.

    Returns:
        bool: True if a config file in ``base`` enables coverage via addopts.
    """
    section_by_file: dict[str, str] = {
        "pytest.ini": "pytest",
        "tox.ini": "pytest",
        "setup.cfg": "tool:pytest",
    }
    for filename in _INI_FILES:
        path = base / filename
        if path.is_file() and _ini_addopts_has_coverage(
            path=path,
            section=section_by_file[filename],
        ):
            return True

    pyproject = base / "pyproject.toml"
    return pyproject.is_file() and _pyproject_addopts_has_coverage(path=pyproject)


def config_addopts_enable_coverage(root: str | Path | None = None) -> bool:
    """Detect whether pytest configuration ``addopts`` enable coverage.

    Scans the project's pytest configuration files for coverage-enabling
    ``--cov`` / ``--cov=`` flags declared in ``addopts``. Walks parent
    directories the same way pytest does when discovering config, stopping
    at the first directory that contains a pytest config file.

    Args:
        root: Directory to search for configuration files. Defaults to the
            current working directory.

    Returns:
        bool: True if the nearest pytest configuration enables coverage via
            ``addopts``.
    """
    base = Path(root) if root is not None else Path.cwd()
    base = base.resolve()

    for directory in (base, *base.parents):
        if _directory_has_pytest_config(base=directory):
            return _directory_enables_coverage(base=directory)
    return False
