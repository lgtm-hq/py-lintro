"""Detection of a project-declared coverage source configuration.

``pytest --cov`` with no value tells ``pytest-cov`` to measure whatever
``coverage.py`` is configured to measure, which is only useful when the project
declares a source (``[tool.coverage.run] source`` in ``pyproject.toml``, a
``.coveragerc``, or a ``[coverage:run]`` section in ``setup.cfg``/``tox.ini``).
Without such a declaration ``coverage.py`` measures every imported module,
including installed dependencies, so lintro keeps emitting ``--cov=.`` there to
preserve the historical "measure this project" behaviour.
"""

from __future__ import annotations

import configparser
import tomllib
from pathlib import Path

_SOURCE_KEYS: tuple[str, ...] = ("source", "source_pkgs", "source_dirs")


def _pyproject_declares_source(path: Path) -> bool:
    """Return whether ``pyproject.toml`` declares a coverage source.

    Args:
        path: Path to the ``pyproject.toml`` file.

    Returns:
        bool: True when ``[tool.coverage.run]`` declares a source key.
    """
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return False
    coverage = tool.get("coverage")
    if not isinstance(coverage, dict):
        return False
    run = coverage.get("run")
    if not isinstance(run, dict):
        return False
    return any(run.get(key) for key in _SOURCE_KEYS)


def _ini_declares_source(path: Path, section: str) -> bool:
    """Return whether an INI/CFG file declares a coverage source.

    Args:
        path: Path to the INI/CFG file.
        section: Section name holding ``coverage.py`` run options.

    Returns:
        bool: True when the section declares a source key.
    """
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return False
    if not parser.has_section(section):
        return False
    return any(parser.get(section, key, fallback="").strip() for key in _SOURCE_KEYS)


def coverage_source_configured(root: str | Path | None = None) -> bool:
    """Detect whether the project declares a ``coverage.py`` source.

    Walks ``root`` and its ancestors the way ``coverage.py`` config discovery
    does, stopping at the first directory that declares a source.

    Args:
        root: Directory to start the search from. Defaults to the cwd.

    Returns:
        bool: True when a coverage source declaration was found.
    """
    base = Path(root) if root is not None else Path.cwd()
    try:
        base = base.resolve()
    except OSError:
        return False
    for directory in (base, *base.parents):
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file() and _pyproject_declares_source(path=pyproject):
            return True
        coveragerc = directory / ".coveragerc"
        if coveragerc.is_file() and _ini_declares_source(
            path=coveragerc,
            section="run",
        ):
            return True
        for name in ("setup.cfg", "tox.ini"):
            candidate = directory / name
            if candidate.is_file() and _ini_declares_source(
                path=candidate,
                section="coverage:run",
            ):
                return True
    return False
