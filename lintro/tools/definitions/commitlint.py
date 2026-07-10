"""Commitlint tool definition.

Commitlint validates commit messages against the Conventional Commits
specification (via a shared config such as ``@commitlint/config-conventional``).

Unlike most lintro tools, commitlint does not operate on files: it inspects
git commit messages. To fit lintro's file-oriented plugin model it mirrors the
git-history-oriented ``gitleaks`` plugin — a broad ``["*"]`` file pattern keeps
shared execution preparation from short-circuiting, and ``check()`` then ignores
the discovered file list and validates the repository's most recent commit
message (``commitlint --last``). This default is deterministic (no ``HEAD~1``
edge case, no network/remote refs) and works in any git repository.

Commitlint requires a config file; when none is present lintro skips the tool as
a non-error rather than failing the run.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.commitlint.commitlint_parser import parse_commitlint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import validate_positive_int
from lintro.tools.core.timeout_utils import create_timeout_result

# Constants for Commitlint configuration
COMMITLINT_DEFAULT_TIMEOUT: int = 30
COMMITLINT_DEFAULT_PRIORITY: int = 35
# Broad pattern so shared execution preparation does not early-return; the
# discovered file list is intentionally ignored (commitlint reads git state).
COMMITLINT_FILE_PATTERNS: list[str] = ["*"]
# Exit code commitlint uses when no config/rules can be resolved.
COMMITLINT_CONFIG_MISSING_EXIT: int = 9


@register_tool
@dataclass
class CommitlintPlugin(BaseToolPlugin):
    """Commit message linter plugin.

    Integrates commitlint with lintro to validate the repository's latest
    commit message against the configured Conventional Commits rules.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="commitlint",
            description=("Conventional Commits linter for git commit messages"),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=COMMITLINT_FILE_PATTERNS,
            priority=COMMITLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[
                "commitlint.config.js",
                "commitlint.config.cjs",
                "commitlint.config.mjs",
                "commitlint.config.ts",
                ".commitlintrc",
                ".commitlintrc.js",
                ".commitlintrc.cjs",
                ".commitlintrc.json",
                ".commitlintrc.yaml",
                ".commitlintrc.yml",
            ],
            version_command=["commitlint", "--version"],
            min_version=get_min_version(ToolName.COMMITLINT),
            default_options={
                "timeout": COMMITLINT_DEFAULT_TIMEOUT,
            },
            default_timeout=COMMITLINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Commitlint-specific options.

        Args:
            timeout: Timeout in seconds (default: 30).
            **kwargs: Other tool options.
        """
        validate_positive_int(timeout, "timeout")

        set_kwargs = dict(kwargs)
        if timeout is not None:
            set_kwargs["timeout"] = timeout

        super().set_options(**set_kwargs)

    def _get_commitlint_command(self) -> list[str]:
        """Get the command used to invoke commitlint.

        Prefers a directly installed ``commitlint`` executable (as provided by
        a global bun/npm install or Docker image) and falls back to ``bunx``.

        Returns:
            Command prefix arguments for invoking commitlint.
        """
        if shutil.which("commitlint"):
            return ["commitlint"]
        if shutil.which("bunx"):
            return ["bunx", "commitlint"]
        return ["commitlint"]

    def doc_url(self, code: str) -> str | None:
        """Return the commitlint rules documentation URL.

        Commitlint documents all rules on a single page, so the specific rule
        code is not used to build the URL.

        Args:
            code: Commitlint rule name (unused; single doc page).

        Returns:
            URL to the commitlint rules documentation, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.COMMITLINT

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Validate the latest commit message with commitlint.

        Args:
            paths: List of file or directory paths (used only to locate the
                git repository / working directory; the file list itself is
                not passed to commitlint).
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Shared preparation handles version check, path validation, and cwd.
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd: list[str] = self._get_commitlint_command()
        cmd.append("--last")

        logger.debug(
            f"[CommitlintPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})",
        )

        try:
            result = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=ctx.timeout,
                cmd=cmd,
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
            )

        combined_output = result.output or ""

        # Prefer stdout (the report), falling back to the combined stream.
        report = result.stdout if result.stdout.strip() else combined_output
        issues = parse_commitlint_output(output=report)

        # No commitlint config present: skip as a non-error rather than fail.
        # The "Please add rules" fallback only applies when no violations were
        # parsed — the report echoes the commit message under "--- input ---",
        # so a commit that merely contains the phrase must not mask a real
        # violation run as a missing-config skip.
        if result.returncode == COMMITLINT_CONFIG_MISSING_EXIT or (
            not issues and "Please add rules" in combined_output
        ):
            skip_message = (
                "Skipping commitlint: no commitlint config found. Add a "
                "commitlint config (e.g. commitlint.config.js extending "
                "@commitlint/config-conventional) to enable commit message "
                "validation."
            )
            logger.debug(f"[CommitlintPlugin] {skip_message}")
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=skip_message,
                issues_count=0,
                skipped=True,
                skip_reason=skip_message,
            )

        issues_count: int = len(issues)
        success_flag: bool = result.success and issues_count == 0

        final_output: str | None = report if report.strip() else None
        if success_flag:
            final_output = None

        return ToolResult(
            name=self.definition.name,
            success=success_flag,
            output=final_output,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Commitlint cannot fix commit messages, only report violations.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Commitlint is a linter only and cannot rewrite
                commit messages.
        """
        raise NotImplementedError(
            "Commitlint cannot fix commit messages; amend the commit to match "
            "the Conventional Commits format.",
        )
