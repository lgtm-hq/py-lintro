"""pip-audit tool definition.

pip-audit is the Python Packaging Authority (PyPA) tool for scanning Python
dependencies for packages with known vulnerabilities. It queries the PyPI
Advisory Database and OSV, complementing bandit (which scans source code) by
scanning the dependency surface instead.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.pip_audit.pip_audit_issue import PipAuditIssue
from lintro.parsers.pip_audit.pip_audit_parser import (
    extract_pip_audit_payload,
    parse_pip_audit_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

# Constants for pip-audit configuration
PIP_AUDIT_DEFAULT_TIMEOUT: int = 120  # Network operations can be slow
PIP_AUDIT_DEFAULT_PRIORITY: int = 90  # High priority for security tool
PIP_AUDIT_FILE_PATTERNS: list[str] = [
    "requirements*.txt",
    "pyproject.toml",
    "setup.py",
]
PIP_AUDIT_REQUIREMENTS_GLOB: str = "requirements*.txt"
PIP_AUDIT_PROJECT_FILES: frozenset[str] = frozenset({"pyproject.toml", "setup.py"})


def _build_targets(files: list[str]) -> list[list[str]]:
    """Group discovered files into pip-audit invocation targets.

    Requirements files are audited via ``-r <file>``; project manifests
    (``pyproject.toml``/``setup.py``) are audited by passing their containing
    directory as pip-audit's positional ``project_path``. Project directories
    are de-duplicated so a project is audited once even when both files exist.

    Args:
        files: Discovered files matching the tool's patterns.

    Returns:
        List of argument groups, one per pip-audit invocation.
    """
    requirement_targets: list[list[str]] = []
    project_dirs: list[str] = []
    for raw in files:
        path = Path(raw)
        if fnmatch(path.name, PIP_AUDIT_REQUIREMENTS_GLOB):
            requirement_targets.append(["-r", str(path)])
        elif path.name in PIP_AUDIT_PROJECT_FILES:
            project_dir = str(path.resolve().parent)
            if project_dir not in project_dirs:
                project_dirs.append(project_dir)

    return requirement_targets + [[project_dir] for project_dir in project_dirs]


def _target_source(target: list[str]) -> str:
    """Return the display source for an audit target.

    Args:
        target: A single pip-audit argument group from :func:`_build_targets`.

    Returns:
        The requirements file path or project directory shown for each issue.
    """
    return target[1] if target[:1] == ["-r"] else target[0]


@register_tool
@dataclass
class PipAuditPlugin(BaseToolPlugin):
    """pip-audit plugin for Lintro.

    Provides security vulnerability scanning for Python dependencies using
    pip-audit to check against the PyPI Advisory Database and OSV.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition with pip-audit configuration.
        """
        return ToolDefinition(
            name="pip_audit",
            description="Security vulnerability scanner for Python dependencies",
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=PIP_AUDIT_FILE_PATTERNS,
            priority=PIP_AUDIT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[],  # pip-audit has no native config file
            version_command=["pip-audit", "--version"],
            min_version=get_min_version(ToolName.PIP_AUDIT),
            default_options={
                "timeout": PIP_AUDIT_DEFAULT_TIMEOUT,
            },
            default_timeout=PIP_AUDIT_DEFAULT_TIMEOUT,
        )

    def set_options(self, **kwargs: Any) -> None:
        """Set tool-specific options.

        Args:
            **kwargs: Options to set, including timeout.

        Raises:
            ValueError: If timeout is negative or not a number.
        """
        if "timeout" in kwargs:
            timeout = kwargs["timeout"]
            if timeout is not None:
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                    raise ValueError("timeout must be a number")
                if timeout < 0:
                    raise ValueError("timeout must be non-negative")
        super().set_options(**kwargs)

    def _build_command(self) -> list[str]:
        """Build the base pip-audit command.

        Returns:
            Command prefix for running pip-audit with JSON output. Per-target
            arguments (``-r <file>`` or a project path) are appended by
            :meth:`check`.
        """
        return ["pip-audit", "--format", "json", "--progress-spinner", "off"]

    def doc_url(self, code: str) -> str | None:
        """Return the OSV advisory URL for the given vulnerability ID.

        pip-audit's advisory IDs (PYSEC/GHSA/CVE) are all resolvable on
        osv.dev.

        Args:
            code: Vulnerability ID (e.g. "PYSEC-2019-217").

        Returns:
            URL to the OSV vulnerability page, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.OSV.format(code=code)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Python dependencies for security vulnerabilities.

        Args:
            paths: List of paths to check.
            options: Additional options for the check.

        Returns:
            ToolResult with security scan results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            # early_result is guaranteed non-None when should_skip is True
            return ctx.early_result  # type: ignore[return-value]

        targets = _build_targets(ctx.files)
        if not targets:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No requirements or project files found; skipping pip-audit.",
                issues_count=0,
            )

        base_cmd = self._build_command()
        issues: list[PipAuditIssue] = []
        output_chunks: list[str] = []
        all_success = True
        parse_failures_count = 0

        for target in targets:
            source = _target_source(target)
            # Run from the target's own directory: requirements files may
            # reference relative paths (editable installs, -r includes) that
            # must resolve against the file's location, not lintro's cwd.
            source_path = Path(source).resolve()
            target_cwd = str(
                source_path if source_path.is_dir() else source_path.parent,
            )
            try:
                proc = self._run_subprocess_result(
                    base_cmd + target,
                    timeout=ctx.timeout,
                    cwd=target_cwd,
                )
            except subprocess.TimeoutExpired:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=f"pip-audit timed out after {ctx.timeout}s",
                    issues_count=0,
                )

            # pip-audit writes its JSON report to stdout; parse stdout only so a
            # stderr warning cannot corrupt parsing (see #1043).
            payload = extract_pip_audit_payload(proc.stdout)
            issues.extend(
                parse_pip_audit_output(proc.stdout, source=source, data=payload),
            )

            # Fail closed on missing or unparseable output. pip-audit is a
            # security scanner: with --format json a successful run always
            # writes a JSON report, so no payload — whether stdout held
            # garbage or nothing at all — must never be reported as a clean
            # pass (see #1044).
            if payload is None:
                all_success = False
                parse_failures_count += 1

            if not proc.success:
                all_success = False
            if proc.output.strip():
                output_chunks.append(proc.output.strip())

        # Overall success: every invocation succeeded AND no issues found.
        overall_success = all_success and len(issues) == 0
        combined_output = "\n".join(output_chunks)
        should_show_output = bool(issues) or not all_success

        return ToolResult(
            name=self.definition.name,
            success=overall_success,
            output=combined_output if should_show_output else None,
            issues_count=len(issues),
            issues=issues if issues else None,
            parse_failures_count=parse_failures_count,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """pip-audit cannot automatically fix vulnerabilities in lintro.

        Args:
            paths: List of paths (unused).
            options: Additional options (unused).

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Always, as lintro does not drive pip-audit's
                experimental ``--fix`` mode.
        """
        raise NotImplementedError(
            "pip-audit cannot automatically fix vulnerabilities via lintro. "
            "Update dependencies manually, e.g. `pip install --upgrade <package>`.",
        )
