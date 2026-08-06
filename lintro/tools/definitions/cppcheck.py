"""Cppcheck tool definition.

Cppcheck is a static analysis tool for C and C++ code. It detects undefined
behavior, memory safety defects, and other bug patterns that compilers commonly
miss, with a design philosophy of minimizing false positives. It runs standalone
on individual translation units and needs no build/project context.

Cppcheck reports its structured findings as XML (schema version 2) on stderr.
Lintro parses that XML natively rather than cppcheck's newer
``--output-format=sarif`` because SARIF is lossy for cppcheck (it collapses the
``style``/``performance``/``portability``/``information`` severities into a single
``warning`` level and drops the ``inconclusive`` flag). See
``docs/tool-analysis/cppcheck-analysis.md``.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.cppcheck.cppcheck_parser import parse_cppcheck_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_list,
    validate_option_types,
)

# Constants for Cppcheck configuration
CPPCHECK_DEFAULT_TIMEOUT: int = 60
CPPCHECK_DEFAULT_PRIORITY: int = 85  # High priority: catches memory-safety defects
CPPCHECK_FILE_PATTERNS: list[str] = [
    "*.c",
    "*.cpp",
    "*.cc",
    "*.cxx",
    "*.c++",
    "*.h",
    "*.hpp",
    "*.hxx",
    "*.h++",
]
# ``error`` severity checks always run; these advisory categories are enabled by
# default to surface actionable, low-false-positive findings. ``unusedFunction``
# and ``information`` are intentionally excluded: the former needs whole-program
# analysis and misfires on per-file runs, the latter is mostly configuration
# noise (missing includes, etc.).
CPPCHECK_DEFAULT_ENABLE: str = "warning,style,performance,portability"
# Cppcheck exits 0 when clean and with this code when any enabled finding is
# reported (see ``--error-exitcode``). Issue counting is driven by the parsed
# XML; the exit code is only used to detect execution failures.
CPPCHECK_ERROR_EXITCODE: int = 1


@register_tool
@dataclass
class CppcheckPlugin(BaseToolPlugin):
    """Cppcheck C/C++ static analysis plugin.

    This plugin integrates Cppcheck with Lintro for detecting bugs and undefined
    behavior in C and C++ source files. Cppcheck is check-only; it does not
    modify code, so ``fix()`` is unsupported.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="cppcheck",
            description=(
                "Static analysis for C/C++ that detects undefined behavior, "
                "memory safety defects, and other bugs"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.SECURITY,
            file_patterns=CPPCHECK_FILE_PATTERNS,
            priority=CPPCHECK_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[],
            version_command=["cppcheck", "--version"],
            min_version=get_min_version(ToolName.CPPCHECK),
            default_options={
                "timeout": CPPCHECK_DEFAULT_TIMEOUT,
                "enable": CPPCHECK_DEFAULT_ENABLE,
                "inconclusive": False,
                "std": None,
                "inline_suppr": False,
                "suppress": None,
            },
            default_timeout=CPPCHECK_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        enable: str | None = None,
        inconclusive: bool | None = None,
        std: str | None = None,
        inline_suppr: bool | None = None,
        suppress: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Cppcheck-specific options.

        Args:
            enable: Comma-separated check categories to enable (e.g.
                ``"warning,style,performance,portability"``). ``error`` checks
                always run regardless of this value.
            inconclusive: Whether to report findings cppcheck cannot fully
                confirm. Increases coverage at the cost of some false positives.
            std: Language standard to assume (e.g. ``c11``, ``c++17``).
            inline_suppr: Whether to honor inline ``// cppcheck-suppress`` comments.
            suppress: List of suppression specifications (e.g.
                ``["missingInclude", "unusedFunction:*"]``).
            **kwargs: Other tool options.

        Raises:
            ValueError: If an option value has the wrong type.
        """
        validate_option_types(
            {"enable": enable, "std": std},
            {"enable": str, "std": str},
        )
        validate_bool(inconclusive, "inconclusive")
        validate_bool(inline_suppr, "inline_suppr")
        validate_list(suppress, "suppress")

        options = filter_none_options(
            enable=enable,
            inconclusive=inconclusive,
            std=std,
            inline_suppr=inline_suppr,
            suppress=suppress,
        )
        super().set_options(**options, **kwargs)

    def _build_command(self, files: list[str]) -> list[str]:
        """Build the cppcheck check command.

        Args:
            files: List of files to check.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = self._get_executable_command("cppcheck")
        # XML (schema v2) report on stderr; quiet suppresses stdout progress.
        cmd.extend(["--xml", "--quiet"])
        cmd.append(f"--error-exitcode={CPPCHECK_ERROR_EXITCODE}")

        enable_opt = self.options.get("enable", CPPCHECK_DEFAULT_ENABLE)
        if enable_opt:
            cmd.append(f"--enable={enable_opt}")

        if self.options.get("inconclusive"):
            cmd.append("--inconclusive")

        std_opt = self.options.get("std")
        if std_opt:
            cmd.append(f"--std={std_opt}")

        if self.options.get("inline_suppr"):
            cmd.append("--inline-suppr")

        suppress_opt = self.options.get("suppress")
        if isinstance(suppress_opt, list):
            for suppression in suppress_opt:
                cmd.append(f"--suppress={suppression}")

        cmd.extend(files)
        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return documentation URL for the given cppcheck check id.

        Cppcheck check ids do not map to per-rule pages, so all codes resolve to
        the manual.

        Args:
            code: Cppcheck check id (e.g., "uninitvar").

        Returns:
            URL to the cppcheck manual, or None if code is empty.
        """
        if code:
            return DocUrlTemplate.CPPCHECK
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Cppcheck for bugs and undefined behavior.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd = self._build_command(files=ctx.files)
        logger.debug(f"[cppcheck] Running: {' '.join(cmd[:8])}...")

        try:
            result = self._run_subprocess_result(cmd=cmd, timeout=ctx.timeout)
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    f"Cppcheck execution timed out ({ctx.timeout}s limit exceeded).\n\n"
                    "Increase the timeout via "
                    "--tool-options cppcheck:timeout=N if needed."
                ),
                issues_count=0,
            )
        except (OSError, ValueError, RuntimeError) as e:
            logger.error(f"Failed to run cppcheck: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"Cppcheck failed: {e}",
                issues_count=0,
            )

        # The XML report is emitted on stderr; stdout is progress noise only.
        issues = parse_cppcheck_output(result.stderr)
        issues_count = len(issues)

        # Cppcheck exits 0 when clean and with CPPCHECK_ERROR_EXITCODE when it
        # reports findings. A non-zero exit with no parseable findings indicates
        # an execution problem (e.g. a bad argument or internal error); fail
        # closed and surface the diagnostic output rather than a silent pass.
        if issues_count == 0 and result.returncode != 0:
            diagnostic = (result.stderr or result.stdout or "").strip()
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=diagnostic or "Cppcheck failed with a non-zero exit code",
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
        """Cppcheck cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Cppcheck does not support fixing issues.
        """
        raise NotImplementedError(
            "Cppcheck cannot automatically fix issues. Run 'lintro check' to see "
            "issues.",
        )
