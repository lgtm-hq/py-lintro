"""PHPStan tool definition.

PHPStan is a static analysis tool for PHP that finds bugs in code without
running it. It infers types, validates function/method signatures, and
reports a wide range of correctness issues at a configurable strictness
``level`` (0-9). It is a check-only tool; it does not modify source files.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
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

# Constants for PHPStan configuration
PHPSTAN_DEFAULT_TIMEOUT: int = 120
PHPSTAN_DEFAULT_PRIORITY: int = 80
PHPSTAN_FILE_PATTERNS: list[str] = ["*.php"]
PHPSTAN_OUTPUT_FORMAT: str = "json"

# PHPStan requires an analysis level (0-9). When the project ships no
# ``phpstan.neon`` config, lintro runs with the most conservative level so
# that standalone files without an autoloader produce the fewest false
# positives. Projects that want stricter analysis add a ``phpstan.neon`` with
# their chosen ``level:`` and lintro defers to it (mirrors ruff/rubocop running
# with defaults, while still respecting native config when present).
PHPSTAN_DEFAULT_LEVEL: int = 0
PHPSTAN_MIN_LEVEL: int = 0
PHPSTAN_MAX_LEVEL: int = 9

PHPSTAN_NATIVE_CONFIGS: list[str] = [
    "phpstan.neon",
    "phpstan.neon.dist",
    "phpstan.dist.neon",
]


@register_tool
@dataclass
class PhpstanPlugin(BaseToolPlugin):
    """PHPStan static analysis plugin.

    This plugin integrates PHPStan with Lintro for static analysis of PHP
    files. It is check-only and does not support automatic fixing.
    """

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
            level: Analysis strictness level (0-9). Ignored when a native
                ``phpstan.neon`` configuration defines the level.
            configuration: Path to a PHPStan configuration file.
            memory_limit: Memory limit passed to PHPStan (e.g. ``512M``).
            **kwargs: Other tool options.

        Raises:
            ValueError: If ``level`` is outside the supported 0-9 range.
        """
        if level is not None:
            validate_int(
                level,
                "level",
                min_value=PHPSTAN_MIN_LEVEL,
                max_value=PHPSTAN_MAX_LEVEL,
            )
        validate_str(configuration, "configuration")
        validate_str(memory_limit, "memory_limit")

        options = filter_none_options(
            level=level,
            configuration=configuration,
            memory_limit=memory_limit,
        )
        super().set_options(**options, **kwargs)

    def _has_native_config(self, run_cwd: str | None = None) -> bool:
        """Check whether a PHPStan configuration file is discoverable.

        PHPStan auto-discovers ``phpstan.neon`` / ``phpstan.neon.dist`` /
        ``phpstan.dist.neon`` from the directory it runs in. When one is
        present it defines the analysis level, so lintro must not also pass
        ``--level`` (which would either be redundant or conflict). The check
        must look at the same directory the subprocess will run from, not
        lintro's own cwd — otherwise a repo-root config can suppress --level
        while the subprocess never discovers that config.

        Args:
            run_cwd: Directory the PHPStan subprocess will execute from;
                defaults to the current working directory.

        Returns:
            True when a native config file exists in the run directory.
        """
        cwd = Path(run_cwd) if run_cwd else Path.cwd()
        return any((cwd / name).is_file() for name in PHPSTAN_NATIVE_CONFIGS)

    def _build_command(
        self,
        files: list[str],
        run_cwd: str | None = None,
    ) -> list[str]:
        """Build the PHPStan invocation command.

        Args:
            files: Relative file paths that should be analysed by PHPStan.
            run_cwd: Directory the subprocess will execute from (used for
                native-config discovery).

        Returns:
            A list of command arguments ready to be executed.
        """
        cmd: list[str] = self._get_executable_command("phpstan")
        cmd.append("analyse")
        cmd.extend(["--error-format", PHPSTAN_OUTPUT_FORMAT])
        cmd.append("--no-progress")
        cmd.append("--no-interaction")

        configuration = self.options.get("configuration")
        has_explicit_config = bool(configuration)
        if configuration:
            cmd.extend(["--configuration", str(configuration)])

        # Only pass --level when the project provides no native config that
        # already defines it (config-defined level wins, like ruff/mypy).
        if not has_explicit_config and not self._has_native_config(run_cwd):
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
                output=(
                    stderr
                    or "PHPStan execution failed with no output.\n"
                    "Re-run with LINTRO_LOG_LEVEL=DEBUG for details."
                ),
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
