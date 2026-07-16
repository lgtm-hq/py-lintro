"""Output manager for timestamped run directories.

This module provides the OutputManager class for managing output
directories and result files for Lintro runs.
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.parsers.base_issue import resolve_issue_code
from lintro.utils.output.constants import (
    DEFAULT_BASE_DIR,
    DEFAULT_KEEP_LAST,
    DEFAULT_RUN_PREFIX,
    DEFAULT_TEMP_PREFIX,
    DEFAULT_TIMESTAMP_FORMAT,
)
from lintro.utils.output.helpers import html_escape, markdown_escape

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult


ACTIVE_RUN_MARKER: str = ".active"

# Matches ANSI CSI sequences (colors, cursor moves) commonly embedded in
# click-styled console output captured by ThreadSafeConsoleLogger.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Docker bind-mount prefix used by CI (-v "$PWD:/code"). Stripping this makes
# report paths repo-relative for readers who view reports outside the container.
_DOCKER_MOUNT_PREFIX = "/code/"


def _strip_ansi(text: str) -> str:
    """Remove ANSI CSI escape sequences from ``text``.

    Args:
        text: Text that may contain ANSI colour/control sequences.

    Returns:
        Text with ANSI sequences removed.
    """
    return _ANSI_ESCAPE_RE.sub("", text)


def _repo_relative_path(file_path: str) -> str:
    """Normalise an issue file path for reports.

    Strips the Docker bind-mount prefix used in CI and, where possible,
    rewrites absolute paths inside the current working directory to be
    repo-relative. Paths outside the workspace are returned unchanged.

    Args:
        file_path: Raw file path captured from tool output.

    Returns:
        A path suitable for inclusion in human-readable reports.
    """
    if not file_path:
        return ""

    if file_path.startswith(_DOCKER_MOUNT_PREFIX):
        return file_path[len(_DOCKER_MOUNT_PREFIX) :]

    if os.path.isabs(file_path):
        try:
            return str(Path(file_path).resolve().relative_to(Path.cwd().resolve()))
        except (OSError, ValueError):
            return file_path

    return file_path


def _format_issue_field(value: str | None) -> str:
    """Return ``-`` for empty issue fields, otherwise the original value.

    Args:
        value: Optional issue metadata field (code, line, message).

    Returns:
        The value or the dash placeholder when empty.
    """
    if value is None:
        return "-"
    stripped = value.strip()
    return stripped or "-"


def _format_issue_line(line: object) -> str:
    """Render an issue line number, showing ``-`` when unknown.

    Args:
        line: Raw line number attribute from an issue.

    Returns:
        A textual representation suitable for reports.
    """
    if line in (None, 0, "0", ""):
        return "-"
    return str(line)


def _issues_suffix(count: int) -> str:
    """Return the ``issue``/``issues`` suffix for a count.

    Args:
        count: Issue count.

    Returns:
        Singular or plural suffix.
    """
    return "issue" if count == 1 else "issues"


class OutputManager:
    """Manages output directories and result files for Lintro runs.

    This class creates a timestamped directory under .lintro/run-{timestamp}/
    and provides methods to write all required output formats.
    """

    def __init__(
        self,
        base_dir: str = DEFAULT_BASE_DIR,
        keep_last: int = DEFAULT_KEEP_LAST,
    ) -> None:
        """Initialize the OutputManager.

        Args:
            base_dir: str: Base directory for output (default: .lintro).
            keep_last: int: Number of runs to keep (default: 10).
        """
        # Allow override via environment variable
        env_base_dir: str | None = os.environ.get("LINTRO_LOG_DIR")
        if env_base_dir:
            self.base_dir = Path(env_base_dir)
        else:
            self.base_dir = Path(base_dir)
        self.keep_last = keep_last
        self.run_dir = self._create_run_dir()
        self._mark_run_dir_active()

    def _create_unique_run_dir(self, base_dir: Path, timestamp: str) -> Path:
        """Create a unique run directory for the given timestamp.

        Args:
            base_dir: Base directory where run directories are stored.
            timestamp: Timestamp string used in the directory name.

        Returns:
            Path to the created run directory.

        Raises:
            RuntimeError: If no unique suffix is available after many attempts.
        """
        base_name = f"{DEFAULT_RUN_PREFIX}{timestamp}-{os.getpid()}"
        max_attempts = 10000

        for suffix in range(max_attempts):
            run_dir = base_dir / f"{base_name}-{suffix:04d}"
            try:
                run_dir.mkdir(parents=True, exist_ok=False)
                return run_dir
            except FileExistsError:
                continue

        raise RuntimeError(
            f"Unable to allocate a unique run directory under {base_dir} for "
            f"{base_name} after {max_attempts} attempts.",
        )

    def _create_run_dir(self) -> Path:
        """Create a new timestamped run directory.

        Returns:
            Path: Path to the created run directory.
        """
        timestamp: str = datetime.datetime.now(tz=datetime.UTC).strftime(
            f"{DEFAULT_TIMESTAMP_FORMAT}-%f",
        )
        try:
            return self._create_unique_run_dir(self.base_dir, timestamp)
        except PermissionError:
            # Fallback to temp directory if not writable. Also redirect
            # self.base_dir so cleanup_old_runs scans the fallback location
            # where the run was actually created.
            temp_base: Path = Path(tempfile.gettempdir()) / DEFAULT_TEMP_PREFIX
            run_dir = self._create_unique_run_dir(temp_base, timestamp)
            logger.warning(
                f"Cannot write to {self.base_dir} (permission denied), "
                f"using fallback: {run_dir}",
            )
            self.base_dir = temp_base
            return run_dir

    def _active_marker_path(self, run_dir: Path) -> Path:
        """Return the marker path for an active run directory."""
        return run_dir / ACTIVE_RUN_MARKER

    def _mark_run_dir_active(self) -> None:
        """Mark the current run directory as active for cleanup protection."""
        marker_path = self._active_marker_path(self.run_dir)
        try:
            marker_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        except OSError as exc:
            logger.warning(
                f"Failed to write active-run marker {marker_path}: {exc}. "
                "Concurrent cleanup may delete this run directory.",
            )

    def _pid_is_active(self, pid: int) -> bool:
        """Check whether a process ID is still alive."""
        if pid <= 0:
            return False

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _is_run_dir_active(self, run_dir: Path) -> bool:
        """Return whether a run directory is still in active use."""
        marker_path = self._active_marker_path(run_dir)
        if not marker_path.exists():
            return False

        try:
            pid = int(marker_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False

        if self._pid_is_active(pid):
            return True

        try:
            marker_path.unlink()
        except OSError as exc:
            logger.debug(
                f"Failed to remove stale active marker {marker_path}: {exc}",
            )
        return False

    def write_console_log(
        self,
        content: str,
    ) -> None:
        """Write the console log to console.log in the run directory.

        Args:
            content: str: The console output as a string.
        """
        (self.run_dir / "console.log").write_text(content, encoding="utf-8")

    def write_json(
        self,
        data: object,
        filename: str = "results.json",
    ) -> None:
        """Write data as JSON to the run directory.

        Args:
            data: object: The data to serialize as JSON.
            filename: str: The output filename (default: results.json).
        """
        with open(self.run_dir / filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def write_markdown(
        self,
        content: str,
        filename: str = "report.md",
    ) -> None:
        """Write Markdown content to the run directory.

        Args:
            content: str: Markdown content as a string.
            filename: str: The output filename (default: report.md).
        """
        (self.run_dir / filename).write_text(content, encoding="utf-8")

    def write_html(
        self,
        content: str,
        filename: str = "report.html",
    ) -> None:
        """Write HTML content to the run directory.

        Args:
            content: str: HTML content as a string.
            filename: str: The output filename (default: report.html).
        """
        (self.run_dir / filename).write_text(content, encoding="utf-8")

    def write_csv(
        self,
        rows: list[list[str]],
        header: list[str],
        filename: str = "summary.csv",
    ) -> None:
        """Write CSV data to the run directory.

        Args:
            rows: list[list[str]]: List of rows (each row is a list of strings).
            header: list[str]: List of column headers.
            filename: str: The output filename (default: summary.csv).
        """
        with open(self.run_dir / filename, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

    def write_reports_from_results(
        self,
        results: list[ToolResult],
        console_text: str | None = None,
    ) -> None:
        """Generate and write Markdown, HTML, and CSV reports from tool results.

        Args:
            results: List of ToolResult objects from a Lintro run.
            console_text: Optional captured console output. When provided, the
                Markdown report mirrors the console dump verbatim (with ANSI
                sequences stripped). When omitted, a structured table layout is
                used as a fallback for callers that do not capture stdout.
        """
        self._write_markdown_report(results=results, console_text=console_text)
        self._write_html_report(results=results)
        self._write_csv_summary(results=results)

    def _write_markdown_report(
        self,
        results: list[ToolResult],
        console_text: str | None = None,
    ) -> None:
        """Write a Markdown report for the current run.

        Args:
            results: List of ToolResult objects from the linting run.
            console_text: Optional captured console output used to render the
                report as a preformatted dump.
        """
        if console_text is not None:
            self.write_markdown(
                content=self._render_markdown_console_dump(console_text=console_text),
            )
            return

        self.write_markdown(content=self._render_markdown_tables(results=results))

    def _render_markdown_console_dump(self, console_text: str) -> str:
        """Render ``report.md`` as a header plus fenced console dump.

        Args:
            console_text: Raw captured console output.

        Returns:
            Markdown content suitable for writing to ``report.md``.
        """
        timestamp = datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds")
        body = _strip_ansi(console_text).rstrip("\n")
        if not body:
            body = "(no console output captured)"
        return (
            "# Lintro Report\n"
            "\n"
            f"_Generated {timestamp} · {self.run_dir.name}_\n"
            "\n"
            "```text\n"
            f"{body}\n"
            "```\n"
        )

    def _render_markdown_tables(self, results: list[ToolResult]) -> str:
        """Render ``report.md`` using per-tool Markdown tables.

        Kept as a fallback for callers that do not capture console output.

        Args:
            results: List of ToolResult objects.

        Returns:
            Markdown content suitable for writing to ``report.md``.
        """
        lines: list[str] = ["# Lintro Report", ""]
        lines.append("## Summary\n")
        lines.append("| Tool | Issues |")
        lines.append("|------|--------|")
        for r in results:
            lines.append(f"| {r.name} | {r.issues_count} |")
        lines.append("")
        for r in results:
            lines.append(
                f"### {r.name} ({r.issues_count} {_issues_suffix(r.issues_count)})",
            )
            if hasattr(r, "issues") and r.issues:
                lines.append("| File | Line | Code | Message |")
                lines.append("|------|------|------|---------|")
                for issue in r.issues:
                    raw_file = getattr(issue, "file", "") or ""
                    file: str = markdown_escape(_repo_relative_path(raw_file))
                    line = _format_issue_line(getattr(issue, "line", None))
                    code: str = markdown_escape(
                        _format_issue_field(resolve_issue_code(issue)),
                    )
                    msg: str = markdown_escape(
                        _format_issue_field(getattr(issue, "message", "") or ""),
                    )
                    lines.append(f"| {file} | {line} | {code} | {msg} |")
                lines.append("")
            else:
                lines.append("No issues found.\n")
        return "\n".join(lines)

    def _write_html_report(
        self,
        results: list[ToolResult],
    ) -> None:
        """Write an HTML report summarizing all tool results and issues.

        Args:
            results: list["ToolResult"]: List of ToolResult objects from the linting
                run.
        """
        html_content: list[str] = [
            "<html><head><title>Lintro Report</title></head><body>",
        ]
        html_content.append("<h1>Lintro Report</h1>")
        html_content.append("<h2>Summary</h2>")
        html_content.append("<table border='1'><tr><th>Tool</th><th>Issues</th></tr>")
        for r in results:
            html_content.append(
                f"<tr><td>{html_escape(r.name)}</td><td>{r.issues_count}</td></tr>",
            )
        html_content.append("</table>")
        for r in results:
            html_content.append(
                f"<h3>{html_escape(r.name)} "
                f"({r.issues_count} {_issues_suffix(r.issues_count)})</h3>",
            )
            if hasattr(r, "issues") and r.issues:
                html_content.append(
                    "<table border='1'><tr><th>File</th><th>Line</th><th>Code</th>"
                    "<th>Message</th></tr>",
                )
                for issue in r.issues:
                    raw_file = getattr(issue, "file", "") or ""
                    file: str = html_escape(_repo_relative_path(raw_file))
                    line = _format_issue_line(getattr(issue, "line", None))
                    code: str = html_escape(
                        _format_issue_field(resolve_issue_code(issue)),
                    )
                    msg: str = html_escape(
                        _format_issue_field(getattr(issue, "message", "") or ""),
                    )
                    html_content.append(
                        f"<tr><td>{file}</td><td>{line}</td><td>{code}</td>"
                        f"<td>{msg}</td></tr>",
                    )
                html_content.append("</table>")
            else:
                html_content.append("<p>No issues found.</p>")
        html_content.append("</body></html>")
        self.write_html(content="\n".join(html_content))

    def _write_csv_summary(
        self,
        results: list[ToolResult],
    ) -> None:
        """Write a CSV summary of all tool results and issues.

        Args:
            results: list["ToolResult"]: List of ToolResult objects from the linting
                run.
        """
        rows: list[list[str]] = []
        header: list[str] = ["tool", "issues_count", "file", "line", "code", "message"]
        for r in results:
            if hasattr(r, "issues") and r.issues:
                for issue in r.issues:
                    raw_file = getattr(issue, "file", "") or ""
                    rows.append(
                        [
                            r.name,
                            str(r.issues_count),
                            _repo_relative_path(raw_file),
                            _format_issue_line(getattr(issue, "line", None)),
                            _format_issue_field(resolve_issue_code(issue)),
                            _format_issue_field(getattr(issue, "message", "") or ""),
                        ],
                    )
            else:
                rows.append([r.name, str(r.issues_count), "", "", "", ""])
        self.write_csv(rows=rows, header=header)

    def cleanup_old_runs(self) -> None:
        """Remove old run directories, keeping only the most recent N runs."""
        if not self.base_dir.exists():
            return
        runs: list[Path] = sorted(
            [
                d
                for d in self.base_dir.iterdir()
                if d.is_dir() and d.name.startswith(DEFAULT_RUN_PREFIX)
            ],
            key=lambda d: d.name,
            reverse=True,
        )
        for old_run in runs[self.keep_last :]:
            if self._is_run_dir_active(old_run):
                continue
            try:
                shutil.rmtree(old_run)
            except FileNotFoundError:
                logger.debug(f"Run directory already removed during cleanup: {old_run}")

    def get_run_dir(self) -> Path:
        """Get the current run directory.

        Returns:
            Path: Path to the current run directory.
        """
        return self.run_dir
