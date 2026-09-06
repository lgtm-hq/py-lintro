"""dotenv-linter tool definition.

dotenv-linter is a fast, Rust-based linter for ``.env`` files. It detects
duplicate keys, lowercase keys, incorrect delimiters, unordered keys, and
other common mistakes, and can automatically fix most of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.dotenv_linter.dotenv_linter_issue import DotenvLinterIssue
from lintro.parsers.dotenv_linter.dotenv_linter_parser import (
    parse_dotenv_linter_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.check_runner import PerFileCheckPolicy, run_per_file_check
from lintro.tools.core.fix_runner import (
    PerFileFixPolicy,
    VerifyMode,
    run_per_file_fix,
)
from lintro.tools.core.option_validators import (
    filter_none_options,
    normalize_str_or_list,
    validate_bool,
)

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue

# Constants for dotenv-linter configuration
DOTENV_LINTER_DEFAULT_TIMEOUT: int = 30
DOTENV_LINTER_DEFAULT_PRIORITY: int = 50
DOTENV_LINTER_FILE_PATTERNS: list[str] = [".env", ".env.*", "*.env"]


def _mark_unfixable(issue: BaseIssue) -> BaseIssue:
    """Clear an issue's ``fixable`` flag after a fix attempt failed to clear it.

    Args:
        issue: Issue that survived a dotenv-linter fix run.

    Returns:
        A copy of the issue that no longer advertises an auto-fix, or the
        issue unchanged when it carries no ``fixable`` flag.
    """
    if isinstance(issue, DotenvLinterIssue):
        return replace(issue, fixable=False)
    return issue


# Convert a CamelCase check name to snake_case for the docs deep-link.
_CAMEL_TO_SNAKE_RE: re.Pattern[str] = re.compile(r"(?<!^)(?=[A-Z])")


@register_tool
@dataclass
class DotenvLinterPlugin(BaseToolPlugin):
    """dotenv-linter ``.env`` file linter plugin.

    Integrates dotenv-linter with Lintro for checking and auto-fixing common
    issues in ``.env`` files.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="dotenv_linter",
            description=(
                "Fast linter for .env files that detects duplicate keys, "
                "lowercase keys, and formatting issues (with auto-fix)"
            ),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=DOTENV_LINTER_FILE_PATTERNS,
            priority=DOTENV_LINTER_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[],
            version_command=["dotenv-linter", "--version"],
            min_version=get_min_version(ToolName.DOTENV_LINTER),
            default_options={
                "timeout": DOTENV_LINTER_DEFAULT_TIMEOUT,
                "recursive": False,
                "exclude": None,
                "skip_checks": None,
                "schema": None,
            },
            default_timeout=DOTENV_LINTER_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        recursive: bool | None = None,
        exclude: list[str] | str | None = None,
        skip_checks: list[str] | str | None = None,
        schema: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set dotenv-linter-specific options.

        Args:
            recursive: Recursively scan directories for ``.env`` files.
            exclude: File or directory paths to exclude from linting/fixing.
            skip_checks: Check names to bypass (e.g., ["LowercaseKey"]).
                Maps to dotenv-linter's ``--ignore-checks``.
            schema: Path to a schema file to validate ``.env`` contents.
            **kwargs: Other tool options.
        """
        validate_bool(recursive, "recursive")
        exclude = normalize_str_or_list(exclude, "exclude")
        skip_checks = normalize_str_or_list(skip_checks, "skip_checks")

        options = filter_none_options(
            recursive=recursive,
            exclude=exclude,
            skip_checks=skip_checks,
            schema=schema,
        )
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return the dotenv-linter docs URL for the given check.

        Args:
            code: Check name (e.g., "LowercaseKey").

        Returns:
            URL to the check's documentation page, or None when no code.
        """
        if not code:
            return None
        slug = _CAMEL_TO_SNAKE_RE.sub("_", code).lower()
        return DocUrlTemplate.DOTENV_LINTER.format(code=slug)

    def _build_common_args(self) -> list[str]:
        """Build CLI arguments shared by the check and fix subcommands.

        Returns:
            List of common CLI arguments.
        """
        args: list[str] = ["--plain"]

        if self.options.get("recursive"):
            args.append("--recursive")

        exclude_opt = self.options.get("exclude")
        if isinstance(exclude_opt, list):
            for path in exclude_opt:
                args.extend(["--exclude", str(path)])

        skip_checks_opt = self.options.get("skip_checks")
        if isinstance(skip_checks_opt, list):
            for check in skip_checks_opt:
                args.extend(["--ignore-checks", str(check)])

        schema_opt = self.options.get("schema")
        if schema_opt is not None:
            args.extend(["--schema", str(schema_opt)])

        return args

    def _check_command(self, file_path: str) -> list[str]:
        """Build the dotenv-linter check command for one file.

        Args:
            file_path: Path to the ``.env`` file to check.

        Returns:
            Command line for dotenv-linter in check mode.
        """
        return [
            *self._get_executable_command(tool_name="dotenv-linter"),
            "check",
            *self._build_common_args(),
            file_path,
        ]

    def _fix_command(self, file_path: str) -> list[str]:
        """Build the dotenv-linter fix command for one file.

        ``--no-backup`` prevents dotenv-linter from writing ``.env.bak``
        files alongside the fixed file.

        Args:
            file_path: Path to the ``.env`` file to fix.

        Returns:
            Command line for dotenv-linter in fix mode.
        """
        return [
            *self._get_executable_command(tool_name="dotenv-linter"),
            "fix",
            "--no-backup",
            *self._build_common_args(),
            file_path,
        ]

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check ``.env`` files with dotenv-linter.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self.prepare(paths=paths, options=options)
        if isinstance(ctx, ToolResult):
            return ctx

        return run_per_file_check(
            ctx,
            plugin=self,
            command=self._check_command,
            parse=lambda output: parse_dotenv_linter_output(output=output),
            policy=PerFileCheckPolicy(
                # dotenv-linter exits non-zero when it reports problems. Treat
                # a non-zero exit with no parsed issues as a genuine failure so
                # real invocation errors are not silently reported as clean.
                failure_message="dotenv-linter check failed",
            ),
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix issues in ``.env`` files with dotenv-linter.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self.prepare(
            paths=paths,
            options=options,
            no_files_message="No files to fix.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        return run_per_file_fix(
            ctx,
            plugin=self,
            check_command=self._check_command,
            fix_command=self._fix_command,
            parse=lambda output: parse_dotenv_linter_output(output=output),
            policy=PerFileFixPolicy(
                check_failure_message="dotenv-linter check failed",
                # Re-check after a successful fix: dotenv-linter cannot fix
                # every check, and surviving issues must not be offered for
                # an auto-fix that was already attempted.
                verify=VerifyMode.AFTER_SUCCESS,
                verify_failure_message="dotenv-linter recheck failed",
                remaining_transform=_mark_unfixable,
                report_verify_output=True,
            ),
        )
