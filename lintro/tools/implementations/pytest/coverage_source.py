"""Detection of a project-declared coverage source configuration.

``pytest --cov`` with no value tells ``pytest-cov`` to measure whatever
``coverage.py`` is configured to measure, which is only useful when the project
declares a source. Without such a declaration ``coverage.py`` measures every
imported module, including installed dependencies, so lintro keeps emitting
``--cov=.`` there to preserve the historical "measure this project" behaviour.

Detection mirrors ``coverage.py``'s own configuration selection: candidate files
are tried in a fixed order (``COVERAGE_RCFILE`` or ``.coveragerc``, then
``.coveragerc.toml``, ``setup.cfg``, ``tox.ini`` and ``pyproject.toml``), the
first one carrying any coverage settings is the active configuration, and only
that file is inspected for a ``run`` source.
"""

from __future__ import annotations

import configparser
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

# Run options that name what coverage.py should measure.
_SOURCE_KEYS: tuple[str, ...] = ("source", "source_pkgs", "source_dirs")

# Section names coverage.py recognises; any of them marks a file as carrying
# coverage settings, which is what ends coverage.py's file search.
_SECTIONS: tuple[str, ...] = (
    "run",
    "report",
    "paths",
    "html",
    "xml",
    "json",
    "lcov",
)

# Files coverage.py tries, paired with whether the file belongs to coverage.py
# alone (an "our file", which may use unprefixed section names).
_CANDIDATES: tuple[tuple[str, bool], ...] = (
    (".coveragerc", True),
    (".coveragerc.toml", True),
    ("setup.cfg", False),
    ("tox.ini", False),
    ("pyproject.toml", False),
)


def _toml_sections(path: Path, our_file: bool) -> dict[str, Mapping[str, object]]:
    """Return the coverage sections a TOML config file declares.

    Args:
        path: Path to the TOML file.
        our_file: Whether unprefixed top-level sections are recognised.

    Returns:
        dict[str, Mapping[str, object]]: Recognised sections by bare name.
    """
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    tool = data.get("tool")
    coverage = tool.get("coverage") if isinstance(tool, dict) else None
    roots: list[Mapping[str, object]] = []
    if isinstance(coverage, dict):
        roots.append(coverage)
    if our_file:
        roots.append(data)
    found: dict[str, Mapping[str, object]] = {}
    for root in roots:
        for name in _SECTIONS:
            section = root.get(name)
            if name not in found and isinstance(section, dict):
                found[name] = section
    return found


def _ini_sections(path: Path, our_file: bool) -> dict[str, Mapping[str, object]]:
    """Return the coverage sections an INI/CFG config file declares.

    Args:
        path: Path to the INI/CFG file.
        our_file: Whether unprefixed section names are recognised.

    Returns:
        dict[str, Mapping[str, object]]: Recognised sections by bare name.
    """
    # coverage.py reads these files without interpolation, so a literal "%" in
    # a value must not raise here either.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return {}
    prefixes = ["coverage:", ""] if our_file else ["coverage:"]
    found: dict[str, Mapping[str, object]] = {}
    for name in _SECTIONS:
        for prefix in prefixes:
            real_name = f"{prefix}{name}"
            if parser.has_section(real_name):
                found[name] = parser[real_name]
                break
    return found


def _sections_for(path: Path, our_file: bool) -> dict[str, Mapping[str, object]]:
    """Return the coverage sections a config file declares.

    Args:
        path: Path to the configuration file.
        our_file: Whether unprefixed section names are recognised.

    Returns:
        dict[str, Mapping[str, object]]: Recognised sections by bare name.
    """
    if path.suffix == ".toml":
        return _toml_sections(path=path, our_file=our_file)
    return _ini_sections(path=path, our_file=our_file)


def _declares_source(sections: Mapping[str, Mapping[str, object]]) -> bool:
    """Return whether a config file's ``run`` section names a source.

    Args:
        sections: Recognised coverage sections by bare name.

    Returns:
        bool: True when a non-empty source option is present.
    """
    run = sections.get("run")
    if run is None:
        return False
    for key in _SOURCE_KEYS:
        value = run.get(key)
        if isinstance(value, str):
            if value.strip():
                return True
        elif value:
            return True
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
    # coverage.py treats COVERAGE_RCFILE as a specified file: it never falls
    # back to the other candidates, it fails instead. Treating an unreadable or
    # settings-free rcfile as "no source" keeps lintro on the safe --cov=. path.
    candidates = [(rcfile, True)] if rcfile else list(_CANDIDATES)

    for filename, our_file in candidates:
        path = Path(filename)
        if not path.is_absolute():
            path = base / filename
        if not path.is_file():
            continue
        sections = _sections_for(path=path, our_file=our_file)
        if not sections:
            continue
        return _declares_source(sections=sections)
    return False
