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


def _token_enables_coverage(token: str) -> bool:
    """Return whether a single addopts token selects a coverage source.

    Report/config-only flags such as ``--cov-report`` / ``--cov-config`` /
    ``--no-cov-on-fail`` do not enable coverage by themselves.

    Args:
        token: One whitespace-delimited addopts token.

    Returns:
        bool: True if the token is ``--cov`` or ``--cov=<path>``.
    """
    cov_flag = "--cov"  # nosec B105 - pytest coverage flag, not a password
    return token == cov_flag or token.startswith(f"{cov_flag}=")


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


def _load_pyproject_toml(path: Path) -> dict[str, object] | None:
    """Load and parse a ``pyproject.toml`` file.

    Args:
        path: Path to the ``pyproject.toml`` file to read.

    Returns:
        dict | None: Parsed TOML data, or None when unreadable.
    """
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError):
        return None


def _pyproject_pytest_tool(data: dict[str, object]) -> dict[str, object] | None:
    """Return the ``[tool.pytest]`` table when it is a valid mapping.

    Args:
        data: Parsed ``pyproject.toml`` data.

    Returns:
        dict | None: The pytest tool table, or None when absent or malformed.
    """
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return None
    pytest_config = tool.get("pytest")
    if not isinstance(pytest_config, dict):
        return None
    return pytest_config


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
    data = _load_pyproject_toml(path)
    if data is None:
        return False
    pytest_config = _pyproject_pytest_tool(data)
    if pytest_config is None:
        return False
    ini_options = pytest_config.get("ini_options")
    if not isinstance(ini_options, dict):
        return False
    return _addopts_has_coverage(ini_options.get("addopts"))


def _ini_has_pytest_section(path: Path, section: str) -> bool:
    """Return whether an INI/CFG file contains a pytest options section.

    Args:
        path: Path to the INI/CFG file.
        section: Section name holding pytest options.

    Returns:
        bool: True when the section exists.
    """
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return False
    return parser.has_section(section)


def _pyproject_has_pytest_config(path: Path) -> bool:
    """Return whether ``pyproject.toml`` declares pytest ini options.

    Args:
        path: Path to the ``pyproject.toml`` file.

    Returns:
        bool: True when ``[tool.pytest.ini_options]`` is present.
    """
    data = _load_pyproject_toml(path)
    if data is None:
        return False
    pytest_config = _pyproject_pytest_tool(data)
    return pytest_config is not None and "ini_options" in pytest_config


def _winning_pytest_config(base: Path) -> Path | None:
    """Return the pytest config file pytest would select in ``base``.

    Pytest precedence in a single directory is ``pytest.ini``, then
    ``pyproject.toml``, then ``tox.ini``, then ``setup.cfg``. Files without a
    pytest section are ignored so the walk can continue to ancestors.

    Args:
        base: Directory that may contain pytest configuration files.

    Returns:
        Path | None: The winning config path, or None when none apply.
    """
    pytest_ini = base / "pytest.ini"
    if pytest_ini.is_file():
        return pytest_ini

    pyproject = base / "pyproject.toml"
    if pyproject.is_file() and _pyproject_has_pytest_config(path=pyproject):
        return pyproject

    tox_ini = base / "tox.ini"
    if tox_ini.is_file() and _ini_has_pytest_section(path=tox_ini, section="pytest"):
        return tox_ini

    setup_cfg = base / "setup.cfg"
    if setup_cfg.is_file() and _ini_has_pytest_section(
        path=setup_cfg,
        section="tool:pytest",
    ):
        return setup_cfg
    return None


def _directory_has_pytest_config(base: Path) -> bool:
    """Return whether ``base`` contains a usable pytest configuration file.

    Args:
        base: Directory that may contain pytest configuration files.

    Returns:
        bool: True when pytest would stop its upward config search here.
    """
    return _winning_pytest_config(base=base) is not None


def _directory_enables_coverage(base: Path) -> bool:
    """Return whether the winning pytest config in ``base`` enables coverage.

    Args:
        base: Directory that may contain pytest configuration files.

    Returns:
        bool: True if the selected config enables coverage via addopts.
    """
    winner = _winning_pytest_config(base=base)
    if winner is None:
        return False
    if winner.name == "pyproject.toml":
        return _pyproject_addopts_has_coverage(path=winner)
    section_by_file = {
        "pytest.ini": "pytest",
        "tox.ini": "pytest",
        "setup.cfg": "tool:pytest",
    }
    return _ini_addopts_has_coverage(
        path=winner,
        section=section_by_file[winner.name],
    )


def config_addopts_enable_coverage(root: str | Path | None = None) -> bool:
    """Detect whether pytest configuration ``addopts`` enable coverage.

    Scans the project's pytest configuration files for coverage-enabling
    ``--cov`` / ``--cov=`` flags declared in ``addopts``. Walks parent
    directories the same way pytest does when discovering config, stopping
    at the first directory that contains a usable pytest config file.

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
