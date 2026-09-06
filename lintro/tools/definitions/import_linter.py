"""Import-linter tool definition.

import-linter (binary ``lint-imports``) checks that a Python project obeys the
import contracts declared in its configuration — layered architectures,
forbidden dependencies, independent subpackages and so on.

The tool is project-scoped: it reads the whole import graph of the configured
root package and evaluates every contract once, so it ignores the discovered
file list and runs a single time per invocation. Configuration lives in
``[tool.importlinter]`` in ``pyproject.toml`` (or ``.importlinter`` /
``setup.cfg``); see docs/tool-analysis/import-linter-analysis.md.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
import tomllib
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.import_linter.import_linter_parser import (
    parse_import_linter_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

# Constants for import-linter configuration
IMPORT_LINTER_DEFAULT_TIMEOUT: int = 60
IMPORT_LINTER_DEFAULT_PRIORITY: int = 50
IMPORT_LINTER_FILE_PATTERNS: list[str] = ["*.py"]

#: Config file names import-linter reads, in the order it prefers them.
IMPORT_LINTER_CONFIG_FILES: tuple[str, ...] = (
    "setup.cfg",
    ".importlinter",
    "pyproject.toml",
)

#: INI section header that marks a file as carrying import-linter configuration.
#: ``setup.cfg`` and ``.importlinter`` use ``[importlinter]`` for session options
#: and ``[importlinter:contract:<id>]`` for each contract.
_INI_SECTION: str = "[importlinter]"
_INI_CONTRACT_PREFIX: str = "[importlinter:contract:"


def _pyproject_declares_import_linter(config_path: Path) -> bool:
    """Report whether a ``pyproject.toml`` carries a ``tool.importlinter`` table.

    The file is parsed rather than scanned line by line: TOML can spell the
    same table several ways (a ``[tool.importlinter]`` header, an inline
    ``importlinter = {{ ... }}`` under ``[tool]``, quoted keys), and a textual
    match silently misses the alternatives — reporting "no configuration" for a
    project that ``lint-imports`` would happily check.

    Args:
        config_path: Path to a ``pyproject.toml``.

    Returns:
        True when the parsed document defines ``tool.importlinter``.
    """
    try:
        with config_path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.debug(f"Could not read import-linter config from {config_path}: {exc}")
        return False
    tool_table = document.get("tool")
    return isinstance(tool_table, dict) and "importlinter" in tool_table


def _ini_declares_import_linter(config_path: Path) -> bool:
    """Report whether an INI-style config declares an import-linter section.

    Args:
        config_path: Path to a ``setup.cfg`` or ``.importlinter`` file.

    Returns:
        True when the file carries ``[importlinter]`` or an
        ``[importlinter:contract:<id>]`` section. Other ``importlinter:``
        prefixed headers are not configuration import-linter reads, so they
        do not make a file a candidate.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(f"Could not read import-linter config from {config_path}: {exc}")
        return False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == _INI_SECTION or line.startswith(_INI_CONTRACT_PREFIX):
            return True
    return False


def _declares_import_linter(config_path: Path) -> bool:
    """Report whether a config file declares an import-linter section.

    Args:
        config_path: Candidate configuration file.

    Returns:
        True when the file contains the section import-linter reads.
    """
    if config_path.name == "pyproject.toml":
        return _pyproject_declares_import_linter(config_path=config_path)
    return _ini_declares_import_linter(config_path=config_path)


def find_import_linter_config(paths: list[str]) -> Path | None:
    """Locate the import-linter configuration governing the given paths.

    Walks upward from each input path, mirroring how ``lint-imports`` resolves
    its own config, and returns the first file that declares an import-linter
    section.

    Args:
        paths: Input file or directory paths passed to the tool.

    Returns:
        Path to the configuration file, or None when no import-linter config
        section is found. Note the predicate is section *presence*, not a
        non-empty contract list: a config declaring only ``root_package`` is
        returned here and ``lint-imports`` runs against its zero contracts.
        When several input paths resolve to different configs the first match
        wins, in the order the paths were given.
    """
    for raw in paths:
        start = Path(raw).resolve()
        if not start.is_dir():
            start = start.parent
        for directory in (start, *start.parents):
            for name in IMPORT_LINTER_CONFIG_FILES:
                candidate = directory / name
                if candidate.is_file() and _declares_import_linter(candidate):
                    return candidate
    return None


@register_tool
@dataclass
class ImportLinterPlugin(BaseToolPlugin):
    """import-linter architectural import-contract checker plugin.

    Runs ``lint-imports`` once per invocation against the project's declared
    contracts and reports one issue per broken import chain.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition with import-linter configuration.
        """
        return ToolDefinition(
            name="import-linter",
            description=(
                "Python import-contract checker that enforces layered "
                "architectures and forbidden dependencies"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=IMPORT_LINTER_FILE_PATTERNS,
            priority=IMPORT_LINTER_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["pyproject.toml", ".importlinter", "setup.cfg"],
            version_command=["lint-imports", "--version"],
            default_options={
                "timeout": IMPORT_LINTER_DEFAULT_TIMEOUT,
            },
            default_timeout=IMPORT_LINTER_DEFAULT_TIMEOUT,
        )

    def _build_command(self, config_path: Path) -> list[str]:
        """Build the ``lint-imports`` command for a resolved config file.

        ``--no-logo`` keeps the ASCII banner out of parsed output and
        ``--no-cache`` stops the tool writing a cache directory into the
        project being checked.

        Args:
            config_path: Configuration file to check contracts from.

        Returns:
            Command list to execute.
        """
        return [
            "lint-imports",
            "--config",
            str(config_path),
            "--no-logo",
            "--no-cache",
        ]

    def doc_url(self, code: str) -> str | None:
        """Return the import-linter contract-types documentation URL.

        import-linter has one documentation page describing every contract
        type, so all codes resolve to it.

        Args:
            code: Contract name (e.g. ``"Layered architecture"``).

        Returns:
            URL to the import-linter documentation, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.IMPORT_LINTER

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check the project's import contracts with import-linter.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with one issue per broken import chain.
        """
        ctx = self.prepare(paths=paths, options=options)
        if isinstance(ctx, ToolResult):
            return ctx

        config_path = find_import_linter_config(paths)
        if config_path is None:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No import-linter configuration found; skipping.",
                issues_count=0,
            )

        cmd = self._build_command(config_path=config_path)
        try:
            success, output = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=str(config_path.parent),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"import-linter timed out after {ctx.timeout}s",
                issues_count=0,
                timed_out=True,
            )

        issues = parse_import_linter_output(output=output)
        return ToolResult(
            name=self.definition.name,
            success=success and not issues,
            output=output,
            issues_count=len(issues),
            issues=issues if issues else None,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """import-linter cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: import-linter cannot fix broken contracts.
        """
        raise NotImplementedError(
            "import-linter cannot automatically fix broken contracts. Run "
            "'lintro check' to see which import chains break them.",
        )
