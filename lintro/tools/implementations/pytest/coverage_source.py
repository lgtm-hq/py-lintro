"""Detection of a project-declared coverage source configuration.

``pytest --cov`` with no value tells ``pytest-cov`` to measure whatever
``coverage.py`` is configured to measure, which is only useful when the project
declares a source. Without such a declaration ``coverage.py`` measures every
imported module, including installed dependencies, so lintro keeps emitting
``--cov=.`` there to preserve the historical "measure this project" behaviour.

Detection mirrors ``coverage.py``'s own configuration selection: the
``COVERAGE_RCFILE`` environment variable wins outright, otherwise the first of
``.coveragerc``, ``setup.cfg``, ``tox.ini`` and ``pyproject.toml`` in the
working directory that carries coverage settings is the active configuration,
and only that file is inspected.
"""

from __future__ import annotations

import configparser
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

_SOURCE_KEYS: tuple[str, ...] = ("source", "source_pkgs", "source_dirs")

# Candidate config files in coverage.py's own precedence order, paired with the
# INI section that holds its run options ("" marks a TOML file).
_CANDIDATES: tuple[tuple[str, str], ...] = (
    (".coveragerc", "run"),
    ("setup.cfg", "coverage:run"),
    ("tox.ini", "coverage:run"),
    ("pyproject.toml", ""),
)


def _load_toml(path: Path) -> dict[str, object] | None:
    """Load a TOML file, returning None when it cannot be parsed.

    Args:
        path: Path to the TOML file.

    Returns:
        dict[str, object] | None: Parsed mapping, or None on failure.
    """
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _toml_run_section(path: Path) -> dict[str, object] | None:
    """Return the ``[tool.coverage.run]`` table of a TOML config file.

    Args:
        path: Path to the TOML file.

    Returns:
        dict[str, object] | None: The run table, or None when absent.
    """
    data = _load_toml(path)
    if data is None:
        return None
    section: object = data
    for key in ("tool", "coverage", "run"):
        if not isinstance(section, dict):
            return None
        section = section.get(key)
    return section if isinstance(section, dict) else None


def _ini_run_section(path: Path, section: str) -> configparser.SectionProxy | None:
    """Return the coverage run section of an INI/CFG config file.

    Args:
        path: Path to the INI/CFG file.
        section: Section name holding ``coverage.py`` run options.

    Returns:
        configparser.SectionProxy | None: The section, or None when absent.
    """
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None
    if not parser.has_section(section):
        return None
    return parser[section]


def _section_declares_source(section: Mapping[str, object]) -> bool:
    """Return whether a coverage run section declares a source.

    Args:
        section: Parsed run section from a TOML table or an INI section.

    Returns:
        bool: True when a non-empty source key is present.
    """
    for key in _SOURCE_KEYS:
        value = section.get(key)
        if isinstance(value, str):
            if value.strip():
                return True
        elif value:
            return True
    return False


def _explicit_config_declares_source(path: Path) -> bool:
    """Return whether an explicitly selected config file declares a source.

    Args:
        path: Path named by ``COVERAGE_RCFILE``.

    Returns:
        bool: True when the file declares a coverage source.
    """
    if not path.is_file():
        return False
    if path.suffix == ".toml" or path.name == "pyproject.toml":
        run = _toml_run_section(path=path)
        return run is not None and _section_declares_source(section=run)
    for candidate_section in ("run", "coverage:run"):
        run_ini = _ini_run_section(path=path, section=candidate_section)
        if run_ini is not None:
            return _section_declares_source(section=run_ini)
    return False


def coverage_source_configured(root: str | Path | None = None) -> bool:
    """Detect whether the active ``coverage.py`` configuration declares a source.

    Args:
        root: Directory to resolve configuration from. Defaults to the cwd.

    Returns:
        bool: True when the active configuration declares a coverage source.
    """
    base = Path(root) if root is not None else Path.cwd()
    rcfile = os.environ.get("COVERAGE_RCFILE")
    if rcfile:
        rc_path = Path(rcfile)
        if not rc_path.is_absolute():
            rc_path = base / rc_path
        return _explicit_config_declares_source(path=rc_path)

    for filename, ini_section in _CANDIDATES:
        path = base / filename
        if not path.is_file():
            continue
        if ini_section:
            run_ini = _ini_run_section(path=path, section=ini_section)
            if run_ini is None:
                continue
            return _section_declares_source(section=run_ini)
        run_toml = _toml_run_section(path=path)
        if run_toml is None:
            continue
        return _section_declares_source(section=run_toml)
    return False
