"""PHPStan tool definition.

PHPStan is a static analysis tool for PHP that finds bugs in code without
running it. It infers types, validates function/method signatures, and
reports a wide range of correctness issues at a configurable strictness
``level`` (0-9). It is a check-only tool; it does not modify source files.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.phpstan.phpstan_parser import parse_phpstan_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_int,
    validate_str,
)
from lintro.utils.unified_config import DEFAULT_TOOL_PRIORITIES

# Constants for PHPStan configuration
PHPSTAN_DEFAULT_TIMEOUT: int = 120
PHPSTAN_DEFAULT_PRIORITY: int = DEFAULT_TOOL_PRIORITIES.get("phpstan", 80)
PHPSTAN_FILE_PATTERNS: list[str] = ["*.php"]
PHPSTAN_OUTPUT_FORMAT: str = "json"

# PHPStan requires an analysis level (0-9). When the project ships no
# ``phpstan.neon`` (or the neon does not define ``level``), lintro runs with
# the most conservative level so standalone files without an autoloader
# produce the fewest false positives. A neon that sets ``level:`` wins unless
# the user passed ``phpstan:level=N``, which is always forwarded.
PHPSTAN_DEFAULT_LEVEL: int = 0
PHPSTAN_MIN_LEVEL: int = 0
PHPSTAN_MAX_LEVEL: int = 9

PHPSTAN_NATIVE_CONFIGS: list[str] = [
    "phpstan.neon",
    "phpstan.neon.dist",
    "phpstan.dist.neon",
]

# Uncommented neon ``level:`` assignment (not a mention inside another value).
_LEVEL_LINE_RE = re.compile(r"^[ \t]*level[ \t]*:")

_CRASH_NO_OUTPUT: str = (
    "PHPStan execution failed with no output.\n"
    "Re-run with LINTRO_LOG_LEVEL=DEBUG for details."
)


def config_defines_level(config_path: Path) -> bool:
    """Return whether a PHPStan neon/config file assigns ``level``.

    PHPStan requires a level from config or ``--level``. A paths-only neon
    does not satisfy that, so lintro must still inject a CLI level.

    Args:
        config_path: Path to a neon (or other) PHPStan configuration file.

    Returns:
        True when an uncommented ``level:`` assignment is present.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if _LEVEL_LINE_RE.match(line):
            return True
    return False


def crash_output(*, stderr: str, stdout: str) -> str:
    """Build ToolResult output for a crashed PHPStan run.

    PHP fatals and truncated JSON land on stdout while stderr often holds
    only the AI-guidance preamble. Both streams must be shown when nothing
    was parsed as issues.

    Args:
        stderr: Captured standard error (may be empty).
        stdout: Captured standard output (may be empty).

    Returns:
        Joined non-empty streams, or a generic failure message.
    """
    joined = "\n".join(part for part in (stderr, stdout) if part)
    return joined or _CRASH_NO_OUTPUT


@register_tool
@dataclass
class PhpstanPlugin(BaseToolPlugin):
    """PHPStan static analysis plugin.

    This plugin integrates PHPStan with Lintro for static analysis of PHP
    files. It is check-only and does not support automatic fixing.
    """

    _level_explicit: bool = field(default=False, init=False, repr=False)

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="phpstan",
            description="Static analysis tool for PHP that finds bugs in code",
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.TYPE_CHECKER,
            file_patterns=PHPSTAN_FILE_PATTERNS,
            priority=PHPSTAN_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=list(PHPSTAN_NATIVE_CONFIGS),
            version_command=["phpstan", "--version"],
            min_version=get_min_version(ToolName.PHPSTAN),
            default_options={
                "timeout": PHPSTAN_DEFAULT_TIMEOUT,
                "level": PHPSTAN_DEFAULT_LEVEL,
                "configuration": None,
                "memory_limit": None,
            },
            default_timeout=PHPSTAN_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        level: int | None = None,
        configuration: str | None = None,
        memory_limit: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set PHPStan-specific options.

        Args:
            level: Analysis strictness level (0-9). Always forwarded as
                ``--level`` when set. The injected default is omitted only
                when a config file actually defines ``level``.
            configuration: Path to a PHPStan configuration file.
            memory_limit: Memory limit passed to PHPStan (e.g. ``512M``).
            **kwargs: Other tool options.
        """
        if level is not None:
            validate_int(
                level,
                "level",
                min_value=PHPSTAN_MIN_LEVEL,
                max_value=PHPSTAN_MAX_LEVEL,
            )
            self._level_explicit = True
        validate_str(configuration, "configuration")
        validate_str(memory_limit, "memory_limit")

        options = filter_none_options(
            level=level,
            configuration=configuration,
            memory_limit=memory_limit,
        )
        super().set_options(**options, **kwargs)

    def _resolve_config_path(self, run_cwd: str | None = None) -> Path | None:
        """Return the PHPStan config file the subprocess will see, if any.

        Args:
            run_cwd: Directory the PHPStan subprocess will execute from;
                defaults to the current working directory.

        Returns:
            Path to an explicit ``--configuration`` file or a native neon in
            the run directory, or None when neither is present.
        """
        cwd = Path(run_cwd) if run_cwd else Path.cwd()
        configuration = self.options.get("configuration")
        if configuration:
            path = Path(str(configuration))
            if not path.is_absolute():
                path = cwd / path
            return path if path.is_file() else None
        for name in PHPSTAN_NATIVE_CONFIGS:
            candidate = cwd / name
            if candidate.is_file():
                return candidate
        return None

    def _should_pass_level(self, run_cwd: str | None = None) -> bool:
        """Return whether ``--level`` should be added to the command.

        Args:
            run_cwd: Directory the subprocess will execute from.

        Returns:
            True when the user set ``phpstan:level`` or the config does not
            define ``level`` (so PHPStan still receives a required level).
        """
        if self._level_explicit:
            return True
        config_path = self._resolve_config_path(run_cwd=run_cwd)
        if config_path is None:
            return True
        return not config_defines_level(config_path)

    def _build_command(
        self,
        files: list[str],
        run_cwd: str | None = None,
    ) -> list[str]:
        """Build the PHPStan invocation command.

        Args:
            files: Relative file paths that should be analysed by PHPStan.
            run_cwd: Directory the subprocess will execute from (used for
                vendor/bin and native-config discovery).

        Returns:
            A list of command arguments ready to be executed.
        """
        cmd: list[str] = self._get_executable_command(
            tool_name="phpstan",
            cwd=run_cwd,
        )
        cmd.append("analyse")
        cmd.extend(["--error-format", PHPSTAN_OUTPUT_FORMAT])
        cmd.append("--no-progress")
        cmd.append("--no-interaction")

        configuration = self.options.get("configuration")
        if configuration:
            cmd.extend(["--configuration", str(configuration)])

        if self._should_pass_level(run_cwd=run_cwd):
            level = self.options.get("level", PHPSTAN_DEFAULT_LEVEL)
            cmd.extend(["--level", str(level)])

        memory_limit = self.options.get("memory_limit")
        if memory_limit:
            cmd.extend(["--memory-limit", str(memory_limit)])

        cmd.extend(files)
        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return the PHPStan documentation URL for an error identifier.

        Args:
            code: PHPStan error identifier (e.g. ``function.notFound``).

        Returns:
            URL to the error-identifier reference page, or None if empty.
        """
        if code:
            return DocUrlTemplate.PHPSTAN.format(code=code)
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with PHPStan.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths=paths, options=options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd = self._build_command(files=ctx.rel_files, run_cwd=ctx.cwd)
        logger.debug(f"[phpstan] Running: {' '.join(cmd[:12])}... (cwd={ctx.cwd})")

        try:
            # PHPStan exits non-zero when it finds errors, so the success flag
            # from the subprocess is not a reliable pass/fail signal. Parse the
            # JSON on stdout independently of the (human-readable) stderr.
            proc = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    f"PHPStan execution timed out ({ctx.timeout}s limit "
                    "exceeded).\n\n"
                    "Increase the timeout via "
                    "--tool-options phpstan:timeout=N."
                ),
                issues_count=0,
            )
        except FileNotFoundError as exc:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    f"phpstan not found: {exc}\n\n"
                    "Please ensure PHP and PHPStan are installed:\n"
                    "  - composer require --dev phpstan/phpstan, or\n"
                    "  - brew install php phpstan"
                ),
                issues_count=0,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            logger.error(f"Failed to run PHPStan: {exc}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"PHPStan execution failed: {exc}",
                issues_count=0,
            )

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        issues = parse_phpstan_output(output=stdout)
        issues_count = len(issues)

        # No JSON on stdout while the process failed means a hard execution
        # error (bad config, missing runtime); surface the diagnostics.
        # PHPStan exits 1 with a JSON report when errors are found (parsed
        # above). A non-zero exit with zero parsed issues — whether stdout was
        # empty, partial JSON, or a PHP fatal error — is a crashed analysis
        # and must never pass as a clean run.
        if issues_count == 0 and proc.returncode != 0:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=crash_output(stderr=stderr, stdout=stdout),
                issues_count=0,
            )

        return ToolResult(
            name=self.definition.name,
            success=issues_count == 0,
            output=None,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """PHPStan does not support auto-fixing.

        Args:
            paths: Paths or files passed for completeness.
            options: Runtime options (unused).

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Always, because PHPStan cannot fix issues.
        """
        raise NotImplementedError(
            "PHPStan cannot automatically fix issues. Run 'lintro check' to see "
            "the static analysis errors that need manual correction.",
        )
