"""Lint integration bridge for AI diff review."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from loguru import logger

from lintro.ai.prompts.review import format_lint_results_section
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools import tool_manager
from lintro.utils.execution.tool_configuration import (
    configure_tool_for_execution,
    get_tools_to_run,
)
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    from pathlib import Path

    from lintro.config.lintro_config import LintroConfig

__all__ = [
    "DEFAULT_LINT_REPORT_ROOT",
    "MAX_LINT_REPORT_BYTES",
    "LintReportError",
    "LintReportIssue",
    "format_lint_results_for_prompt",
    "load_lint_report",
    "restrict_lint_results_to_files",
    "run_lint_on_changed_files",
]

#: Largest saved report ``load_lint_report`` will read. The report is
#: PR-controlled data (#2571): it comes from the untrusted lint job's artifact,
#: so it gets a hard byte ceiling before parsing, the same way the diff has a
#: prompt budget. A lintro JSON report for a whole PR is tens of kilobytes.
MAX_LINT_REPORT_BYTES: Final[int] = 4 * 1024 * 1024

#: Repository mount root inside the container the untrusted lint job runs
#: in. Both producers of the report the dogfood review reads mount the
#: checkout here: ``scripts/ci/dogfood-changed-files.sh`` (``-v $(pwd):/code
#: -w /code``) and lgtm-ci's ``run-lintro-docker.sh`` behind the reusable
#: full-repo lint. An absolute report path is repository-relative only after
#: this exact prefix is stripped; any other absolute path is outside the
#: review and dropped.
DEFAULT_LINT_REPORT_ROOT: Final[str] = "/code"


class LintReportError(ValueError):
    """A saved lintro JSON report cannot be used as review input.

    Raised by :func:`load_lint_report` for a missing, oversized, unreadable,
    or structurally invalid report. Callers turn it into a review-header
    warning rather than a failed review: linter facts are an optional input.
    """


@dataclass
class LintReportIssue(BaseIssue):
    """One issue rehydrated from a saved lintro JSON report.

    ``BaseIssue`` has no ``code`` field of its own (each tool's issue class
    supplies it); the report serializer already resolved the canonical code,
    so this carries it verbatim.

    Attributes:
        code: Rule code as written by the report's serializer.
    """

    code: str = field(default="")


def run_lint_on_changed_files(
    *,
    changed_files: list[str],
    lintro_config: LintroConfig,
) -> list[ToolResult]:
    """Run lintro check tools scoped to changed files without AI enhancement.

    Args:
        changed_files: Repository-relative changed file paths.
        lintro_config: Loaded Lintro configuration.

    Returns:
        Raw tool results from applicable linters.
    """
    if not changed_files:
        return []

    # Tool selection triggers plugin discovery and the config manager reads
    # the project's native tool configs from disk. Either can raise, and
    # neither failure is a reason to abort the review: the digest is an
    # optional fact layer, so a setup failure drops it and the review runs
    # from the diff alone (#2571). Per-tool failures are handled below.
    try:
        selection = get_tools_to_run(
            tools="all",
            action=Action.CHECK,
            lintro_config=lintro_config,
        )
        if not selection.to_run:
            return []
        config_manager = UnifiedConfigManager()
    except Exception:
        logger.warning(
            "Lint bridge skipped: could not select or configure tools",
            exc_info=True,
        )
        return []

    results: list[ToolResult] = []
    # Every tool this bridge is about to run. Format authority is resolved
    # from the run's selection (#1742), so passing the real set keeps the
    # bridge's ruff/black split identical to `lintro chk`; an empty set would
    # leave ruff's format_check on and duplicate black's findings.
    selected_tools = set(selection.to_run)

    for tool_name in selection.to_run:
        try:
            tool = tool_manager.get_tool(tool_name)
        except (KeyError, ValueError):
            continue

        try:
            tool = configure_tool_for_execution(
                tool=tool,
                tool_name=tool_name,
                config_manager=config_manager,
                tool_option_dict={},
                exclude=None,
                include_venv=False,
                incremental=False,
                action=Action.CHECK,
                selected_tools=selected_tools,
                auto_install=False,
                lintro_config=lintro_config,
            )
            result = tool.check(paths=changed_files, options={})
        except Exception:
            logger.warning(
                "Lint bridge skipped {} after check failure",
                tool_name,
                exc_info=True,
            )
            continue
        results.append(result)

    return results


def format_lint_results_for_prompt(
    *,
    results: list[ToolResult],
    max_entries: int = 200,
) -> str:
    """Format lint tool results as a compact prompt digest.

    Args:
        results: Tool results from ``run_lint_on_changed_files``.
        max_entries: Maximum number of issue entries to include.

    Returns:
        Lint digest wrapped in ``<lint_results>`` tags, or empty string.
    """
    lines: list[str] = []
    for result in results:
        if not result.issues:
            continue
        for issue in result.issues:
            if len(lines) >= max_entries:
                break
            code = getattr(issue, "code", "") or "unknown"
            message = getattr(issue, "message", "") or ""
            file_path = getattr(issue, "file", "") or ""
            line_no = getattr(issue, "line", None)
            line_suffix = f" | line: {line_no}" if line_no else ""
            lines.append(
                f"Tool: {result.name} | file: {file_path}{line_suffix}\n"
                f"Code: {code} | Message: {message}",
            )
        if len(lines) >= max_entries:
            break

    if not lines:
        return ""

    digest = "\n\n".join(lines)
    if len(lines) >= max_entries:
        digest += "\n\n(truncated lint digest)"
    return format_lint_results_section(digest=digest)


def _report_field(entry: Any, key: str, *, where: str) -> Any:
    """Return ``entry[key]`` from a report mapping, or raise.

    Args:
        entry: Candidate mapping from the report.
        key: Key that must be present.
        where: Location description for the error message.

    Returns:
        The value under ``key``.

    Raises:
        LintReportError: When ``entry`` is not a mapping or lacks ``key``.
    """
    if not isinstance(entry, dict):
        msg = f"{where} is not an object"
        raise LintReportError(msg)
    if key not in entry:
        msg = f"{where} has no '{key}' field"
        raise LintReportError(msg)
    return entry[key]


def _parse_report_issue(raw: Any, *, where: str) -> LintReportIssue:
    """Validate and convert one serialized issue.

    Args:
        raw: Serialized issue mapping from the report.
        where: Location description for error messages.

    Returns:
        The rehydrated issue.

    Raises:
        LintReportError: When a required field is missing or mistyped.
    """
    file_path = _report_field(raw, "file", where=where)
    message = _report_field(raw, "message", where=where)
    code = raw.get("code", "")
    line = raw.get("line", 0)
    if not isinstance(file_path, str) or not isinstance(message, str):
        msg = f"{where} has a non-string 'file' or 'message'"
        raise LintReportError(msg)
    if not isinstance(code, str):
        msg = f"{where} has a non-string 'code'"
        raise LintReportError(msg)
    if line is None:
        line = 0
    if isinstance(line, bool) or not isinstance(line, int):
        msg = f"{where} has a non-integer 'line'"
        raise LintReportError(msg)
    return LintReportIssue(
        file=file_path,
        line=line,
        message=message,
        code=code,
    )


def load_lint_report(path: Path) -> list[ToolResult]:
    """Load a saved lintro JSON report as tool results.

    Reads the document ``lintro chk --output-format json`` (and the
    ``.lintro/artifacts/json/results.json`` side channel) writes and converts
    each per-tool entry into the same shape :func:`run_lint_on_changed_files`
    returns, so the two feed :func:`format_lint_results_for_prompt`
    identically (#2571). Only ``file``, ``line``, ``code`` and ``message`` are
    carried per issue: that is all the prompt digest renders.

    The report is untrusted input. It is size-capped before parsing and
    validated structurally; anything that does not look like a lintro report
    raises rather than being coerced.

    Args:
        path: Path to the JSON report.

    Returns:
        One ``ToolResult`` per tool entry, in report order.

    Raises:
        LintReportError: When the file is missing, too large, not JSON, or
            not shaped like a lintro report.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        msg = f"lint report not readable: {path} ({exc.strerror or exc})"
        raise LintReportError(msg) from exc
    if size > MAX_LINT_REPORT_BYTES:
        msg = (
            f"lint report too large: {path} is {size} bytes "
            f"(limit {MAX_LINT_REPORT_BYTES})"
        )
        raise LintReportError(msg)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"lint report is not valid JSON: {path} ({exc})"
        raise LintReportError(msg) from exc

    raw_results = _report_field(document, "results", where="lint report")
    if not isinstance(raw_results, list):
        msg = "lint report 'results' is not a list"
        raise LintReportError(msg)

    results: list[ToolResult] = []
    for index, entry in enumerate(raw_results):
        where = f"lint report results[{index}]"
        name = _report_field(entry, "tool", where=where)
        if not isinstance(name, str) or not name:
            msg = f"{where} has an empty or non-string 'tool'"
            raise LintReportError(msg)
        raw_issues = entry.get("issues", [])
        if raw_issues is None:
            raw_issues = []
        if not isinstance(raw_issues, list):
            msg = f"{where} 'issues' is not a list"
            raise LintReportError(msg)
        issues = [
            _parse_report_issue(raw, where=f"{where}.issues[{position}]")
            for position, raw in enumerate(raw_issues)
        ]
        results.append(
            ToolResult(
                name=name,
                success=bool(entry.get("success", True)),
                issues_count=len(issues),
                issues=issues,
            ),
        )
    return results


def _normalize_report_path(raw: str) -> str:
    """Normalize a report file path for changed-file matching.

    Args:
        raw: Path as written in the report.

    Returns:
        Forward-slash path with any leading ``./`` removed.
    """
    text = raw.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _relative_report_path(report_path: str, *, report_root: str) -> str | None:
    """Return a report path as repository-relative, or ``None`` if it is not.

    A relative path is already repository-relative. An absolute path is
    repository-relative only when it sits under ``report_root``, the mount
    root the lint container ran in; the prefix is stripped exactly, so
    ``/code/tests/lintro/x.py`` becomes ``tests/lintro/x.py`` and can never
    stand in for ``lintro/x.py``. An absolute path anywhere else is not a
    file of this repository as far as the review can tell.

    Args:
        report_path: Normalized path from the report.
        report_root: Absolute container mount root, without a trailing slash.

    Returns:
        The repository-relative path, or ``None`` when the path is absolute
        and outside ``report_root``.
    """
    if not report_path.startswith("/"):
        return report_path
    prefix = report_root.rstrip("/") + "/"
    if report_path.startswith(prefix):
        return report_path[len(prefix) :]
    return None


def restrict_lint_results_to_files(
    *,
    results: list[ToolResult],
    changed_files: list[str],
    report_root: str = DEFAULT_LINT_REPORT_ROOT,
) -> list[ToolResult]:
    """Keep only the issues that fall on the review's changed files.

    A saved report may cover more than the diff under review (a full-repo
    lint, or a lint of a broader change set), and facts about files the
    reviewer cannot see would only invite the model to cite them (#2571).
    Paths match exactly once made repository-relative; an absolute path
    outside ``report_root`` is dropped and counted in the log.

    Args:
        results: Tool results, typically from :func:`load_lint_report`.
        changed_files: Repository-relative changed file paths.
        report_root: Container mount root the report's absolute paths are
            relative to. Defaults to :data:`DEFAULT_LINT_REPORT_ROOT`.

    Returns:
        New tool results carrying only the matching issues, one per input
        result, with ``issues_count`` recomputed.
    """
    changed = frozenset(_normalize_report_path(path) for path in changed_files)
    restricted: list[ToolResult] = []
    outside_root = 0
    for result in results:
        kept = []
        for issue in result.issues or ():
            relative = _relative_report_path(
                _normalize_report_path(getattr(issue, "file", "") or ""),
                report_root=report_root,
            )
            if relative is None:
                outside_root += 1
                continue
            if relative in changed:
                kept.append(issue)
        restricted.append(
            ToolResult(
                name=result.name,
                success=result.success,
                issues_count=len(kept),
                issues=kept,
            ),
        )
    if outside_root:
        logger.info(
            "Lint report: dropped {} issue(s) on absolute paths outside {}",
            outside_root,
            report_root,
        )
    return restricted
