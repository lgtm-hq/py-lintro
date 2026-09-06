"""Pylint tool definition.

pylint is a static analyser for Python. Lintro wires it in for the checks no
other bundled tool covers — chiefly ``duplicate-code`` (``R0801``), which finds
copy-pasted blocks across modules — and leaves the rest of pylint's catalogue
to the project's own ``[tool.pylint]`` configuration.

The plugin is project-scoped: pylint's cross-module checkers only see clones
that appear in a single invocation, so every discovered file is passed to one
``pylint`` run rather than being checked file by file. Check-only: pylint
reports problems and never rewrites source.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - used safely with shell disabled
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.pylint.pylint_parser import parse_pylint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

# Constants for pylint configuration.
# pylint walks the full AST of every module and the similarity checker compares
# every block against every other, so it is orders of magnitude slower than
# ruff: this repo's own `lintro` + `tests` tree takes ~5 minutes. The default
# has to clear a whole-repo run, not a single package, or the tool times out
# exactly where it is most useful.
PYLINT_DEFAULT_TIMEOUT: int = 900
PYLINT_DEFAULT_PRIORITY: int = 50
PYLINT_FILE_PATTERNS: list[str] = ["*.py", "*.pyi"]
PYLINT_OUTPUT_FORMAT: str = "json2"

#: Config file names pylint reads, in its own preference order (see
#: ``pylint.config.find_default_config_files``). Dedicated rc files win over
#: the shared files, and the shared files count only when they declare a
#: pylint section.
PYLINT_CONFIG_FILES: tuple[str, ...] = (
    "pylintrc",
    "pylintrc.toml",
    ".pylintrc",
    ".pylintrc.toml",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
)

#: Dedicated INI rc files: their whole purpose is configuring pylint, so they
#: need no section check.
_DEDICATED_INI_CONFIGS: frozenset[str] = frozenset({"pylintrc", ".pylintrc"})

#: Config files parsed as TOML. The dedicated ``*.toml`` rc files still have to
#: carry a ``tool.pylint`` table — that is what pylint looks for in them.
_TOML_CONFIGS: frozenset[str] = frozenset(
    {"pylintrc.toml", ".pylintrc.toml", "pyproject.toml"},
)

#: Informational line pylint prints — with a *usage-error* exit status — when
#: no files remain to check after its own ignore filters have run, i.e. it was
#: handed nothing to analyse. It is not a report and not a failure. The phrase
#: is only meaningful outside a json2 report: an ``R0801`` body quotes the
#: duplicated source, so a report can legitimately contain it.
PYLINT_NOTHING_TO_LINT: str = "No files to lint"

#: INI section prefix marking pylint configuration in a ``setup.cfg`` or
#: ``tox.ini``.
_INI_SECTION_PREFIX: str = "[pylint"

#: ``ToolResult.metadata`` marker set only on a result built from a real pylint
#: report. The duplicate-code gate (``lintro/utils/duplicate_code.py``) requires
#: it before it reports a verdict, so a run that analysed nothing — no matching
#: files, or none under ``include`` — cannot be read as "no duplication".
PYLINT_ANALYSED_METADATA_KEY: str = "pylint_analysed"

#: Message shown when every discovered file was filtered out by ``include``.
PYLINT_NO_INCLUDED_FILES: str = (
    "No Python files under the configured pylint include paths."
)


def _normalize_include_prefix(value: object) -> str:
    """Normalize one ``include`` entry into a POSIX path prefix.

    Args:
        value: Raw entry from the ``include`` option.

    Returns:
        The prefix with backslashes folded to ``/`` and leading ``./`` and
        surrounding ``/`` removed. Empty when the entry is blank.
    """
    text = str(value).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def filter_included_files(
    *,
    files: list[str],
    prefixes: tuple[str, ...],
) -> list[str]:
    """Restrict discovered files to those under the configured include paths.

    pylint has no built-in way to scope a project-wide run to a subtree while
    still reading the project config, so the scoping is applied here. Paths are
    matched as prefixes of each file path relative to the run's working
    directory, so ``lintro/tools/definitions`` selects that package and nothing
    else.

    Args:
        files: Discovered file paths, relative to the run's working directory.
        prefixes: Normalized include prefixes. Empty means no filtering.

    Returns:
        The files under one of the prefixes, or all files when no prefix is
        configured.
    """
    if not prefixes:
        return files
    kept: list[str] = []
    for path in files:
        normalized = _normalize_include_prefix(path)
        if any(
            normalized == prefix or normalized.startswith(f"{prefix}/")
            for prefix in prefixes
        ):
            kept.append(path)
    return kept


def _toml_declares_pylint(config_path: Path) -> bool:
    """Report whether a TOML config carries a ``tool.pylint`` table.

    The file is parsed rather than scanned line by line: TOML can spell the
    same table several ways (``[tool.pylint.main]``, an inline
    ``pylint = {{ ... }}`` under ``[tool]``, quoted keys), and a textual match
    silently misses the alternatives.

    Args:
        config_path: Path to a ``pyproject.toml``, ``pylintrc.toml`` or
            ``.pylintrc.toml``.

    Returns:
        True when the parsed document defines ``tool.pylint``.
    """
    try:
        with config_path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.debug(f"Could not read pylint config from {config_path}: {exc}")
        return False
    tool_table = document.get("tool")
    return isinstance(tool_table, dict) and "pylint" in tool_table


def _ini_declares_pylint(config_path: Path) -> bool:
    """Report whether an INI-style config declares a pylint section.

    Args:
        config_path: Path to a ``setup.cfg`` or ``tox.ini``.

    Returns:
        True when the file carries a ``[pylint...]`` section header.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(f"Could not read pylint config from {config_path}: {exc}")
        return False
    return any(
        line.strip().startswith(_INI_SECTION_PREFIX) for line in text.splitlines()
    )


def _declares_pylint(config_path: Path) -> bool:
    """Report whether a config file holds pylint configuration.

    ``pylintrc`` and ``.pylintrc`` exist only to configure pylint, so their
    presence is enough. Every other candidate has to declare a pylint section,
    exactly as pylint's own config resolution requires — including the
    ``*.toml`` rc files, which pylint reads only for a ``tool.pylint`` table.

    Args:
        config_path: Candidate configuration file.

    Returns:
        True when pylint would read configuration from this file.
    """
    if config_path.name in _DEDICATED_INI_CONFIGS:
        return True
    if config_path.name in _TOML_CONFIGS:
        return _toml_declares_pylint(config_path=config_path)
    return _ini_declares_pylint(config_path=config_path)


def _format_message_list(value: object) -> str | None:
    """Render a ``--disable``/``--enable`` option value for pylint's CLI.

    ``--tool-options`` splits its own entries on commas, so a multi-value
    option is written pipe-separated (``pylint:disable=all|R0801``) and reaches
    the plugin as a list. pylint's own flags take a comma-separated list, so
    the parts are rejoined with commas; stringifying the list directly would
    hand pylint a Python repr.

    Args:
        value: Raw option value: a string, a list of strings, or None.

    Returns:
        The comma-separated value for the flag, or None when nothing is set.
    """
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
        return ",".join(parts) or None
    text = str(value).strip()
    return text or None


def find_pylint_config(paths: list[str]) -> Path | None:
    """Locate the pylint configuration governing the given paths.

    Walks upward from each input path, mirroring pylint's own resolution, and
    returns the first file that actually declares pylint configuration.

    Args:
        paths: Input file or directory paths passed to the tool.

    Returns:
        Path to the configuration file, or None when no pylint configuration
        is found. When several input paths resolve to different configs the
        first match wins, in the order the paths were given.
    """
    for raw in paths:
        start = Path(raw).resolve()
        if not start.is_dir():
            start = start.parent
        for directory in (start, *start.parents):
            for name in PYLINT_CONFIG_FILES:
                candidate = directory / name
                if candidate.is_file() and _declares_pylint(candidate):
                    return candidate
    return None


@register_tool
@dataclass
class PylintPlugin(BaseToolPlugin):
    """pylint static-analysis plugin.

    Runs ``pylint`` once per invocation over every discovered Python file so
    cross-module checkers such as ``duplicate-code`` can see the whole set.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition with pylint configuration.
        """
        return ToolDefinition(
            name="pylint",
            description=(
                "Python static analyser; finds duplicate code and other "
                "cross-module defects"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=PYLINT_FILE_PATTERNS,
            priority=PYLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=list(PYLINT_CONFIG_FILES),
            version_command=["pylint", "--version"],
            # json2 is the reporter this plugin parses; it first shipped in
            # pylint 3.2.
            min_version="3.2.0",
            default_options={
                "timeout": PYLINT_DEFAULT_TIMEOUT,
                "disable": None,
                "enable": None,
                "include": None,
            },
            default_timeout=PYLINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        disable: str | list[str] | None = None,
        enable: str | list[str] | None = None,
        include: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Set pylint-specific options.

        Args:
            disable: Messages/categories to disable, forwarded to
                ``--disable=``. A pipe-separated ``--tool-options`` value
                arrives here as a list.
            enable: Messages/categories to enable, forwarded to ``--enable=``.
                A pipe-separated ``--tool-options`` value arrives here as a
                list.
            include: Path prefixes the run is scoped to. Only discovered files
                under one of them are analysed; everything else is dropped
                before pylint is invoked. Unset means every discovered file.
            **kwargs: Other tool options (e.g. ``timeout``). Keys owned by the
                duplicate-code gate (``duplicate_code_baseline``) are accepted
                and ignored here: they configure
                ``lintro/utils/duplicate_code.py``, not the pylint command.
        """
        options: dict[str, Any] = {
            "disable": disable,
            "enable": enable,
            "include": include,
        }
        options = {key: value for key, value in options.items() if value is not None}
        super().set_options(**options, **kwargs)

    def _include_prefixes(self) -> tuple[str, ...]:
        """Return the normalized ``include`` prefixes for this invocation.

        Returns:
            Normalized, non-empty path prefixes. Empty when ``include`` is
            unset, so the run is not scoped.
        """
        raw = self.options.get("include")
        if raw is None:
            return ()
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        prefixes = (_normalize_include_prefix(value) for value in values)
        return tuple(prefix for prefix in prefixes if prefix)

    def _build_check_command(
        self,
        files: list[str],
        config_path: Path | None,
    ) -> list[str]:
        """Build the pylint check command.

        Args:
            files: Files to check, in one invocation.
            config_path: Resolved pylint configuration file, or None when the
                project has none and pylint's built-in defaults apply.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = self._get_executable_command("pylint")
        cmd.append(f"--output-format={PYLINT_OUTPUT_FORMAT}")
        if config_path is not None:
            cmd.extend(["--rcfile", str(config_path)])

        # The ``--flag=value`` form is required, not cosmetic: pylint's
        # disable/enable actions treat a space-separated value as a usage
        # error (exit 32) instead of a message list.
        disable = _format_message_list(self.options.get("disable"))
        if disable:
            cmd.append(f"--disable={disable}")

        enable = _format_message_list(self.options.get("enable"))
        if enable:
            cmd.append(f"--enable={enable}")

        cmd.extend(files)
        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return the pylint messages documentation URL for a message id.

        pylint's per-message pages are keyed by category and symbol rather
        than by message id, so every code resolves to the overview page that
        indexes them.

        Args:
            code: Pylint message id (e.g. ``"R0801"``).

        Returns:
            URL to the pylint messages overview, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.PYLINT

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Python files with pylint.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with one issue per pylint message.
        """
        ctx = self.prepare(paths=paths, options=options)
        if isinstance(ctx, ToolResult):
            return ctx

        files = filter_included_files(
            files=ctx.rel_files,
            prefixes=self._include_prefixes(),
        )
        if not files:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=PYLINT_NO_INCLUDED_FILES,
                issues_count=0,
            )

        config_path = find_pylint_config(paths)
        cmd = self._build_check_command(files=files, config_path=config_path)
        logger.debug(
            f"[pylint] Running: {' '.join(cmd[:8])}... ({len(files)} files)",
        )

        try:
            result = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    f"pylint timed out after {ctx.timeout}s. Raise it with "
                    "--tool-options pylint:timeout=N."
                ),
                issues_count=0,
                timed_out=True,
            )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if stderr:
            logger.debug(f"[pylint] stderr: {stderr[:500]}")

        if not stdout.startswith("{"):
            # pylint prints usage errors (a bad rcfile, an unknown message id)
            # to stderr with no report at all. A report is always JSON, so
            # non-JSON plus a non-zero exit is an execution failure, while
            # non-JSON with a zero exit is informational and clean.
            if PYLINT_NOTHING_TO_LINT in stdout or PYLINT_NOTHING_TO_LINT in stderr:
                # pylint exits 32 here even though nothing went wrong, so this
                # has to be recognised before the exit code is consulted. The
                # check is confined to the no-report branch: a json2 report
                # can carry the same phrase inside an ``R0801`` body.
                logger.debug("[pylint] Nothing left to lint; treating as a clean pass")
                return ToolResult(
                    name=self.definition.name,
                    success=True,
                    output=None,
                    issues_count=0,
                )
            if result.returncode != 0:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=stderr or stdout or "pylint failed with no output",
                    issues_count=0,
                )
            logger.debug(f"[pylint] Non-report output treated as clean: {stdout!r}")
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=None,
                issues_count=0,
            )

        try:
            issues = parse_pylint_output(output=stdout)
        except json.JSONDecodeError as exc:
            # Unreadable output must never be reported as a clean pass.
            logger.error(f"Failed to parse pylint output: {exc}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=stdout,
                issues_count=0,
                parse_failures_count=1,
            )

        return ToolResult(
            name=self.definition.name,
            success=not issues,
            output=None,
            issues_count=len(issues),
            issues=issues if issues else None,
            metadata={PYLINT_ANALYSED_METADATA_KEY: True},
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Pylint cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: pylint has no fix mode.
        """
        raise NotImplementedError(
            "pylint cannot automatically fix issues. Run 'lintro check' to see "
            "what it reports.",
        )
