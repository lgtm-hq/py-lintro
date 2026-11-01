"""Deterministic finding identities and conservative baseline maintenance."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from pydantic import ValidationError

from lintro.models.core.fingerprint_baseline import (
    BaselineEntry,
    BaselineFinding,
    FingerprintBaseline,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.tool_result import ToolResult

Fingerprint = tuple[str, str, str, str]
FindingT = TypeVar("FindingT", bound=BaselineFinding)


def relative_file(file: str, *, root: Path, cwd: str | None = None) -> str | None:
    """Canonicalize a diagnostic path without accepting paths outside the root.

    Args:
        file: Producer's diagnostic path.
        root: Invocation workspace root.
        cwd: Producer's working directory, if relative paths use it.

    Returns:
        Canonical relative path, or None for an unacknowledgeable path.
    """
    if not file:
        return None
    try:
        path = Path(file)
        if not path.is_absolute():
            path = (Path(cwd) if cwd else root) / path
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        return BaselineEntry.canonical_file(relative)
    except (OSError, ValueError, RuntimeError):
        return None


def fingerprint(
    issue: BaselineFinding,
    *,
    tool: str,
    root: Path,
    cwd: str | None = None,
) -> BaselineEntry | None:
    """Build a stable fingerprint independent of line and column shifts.

    Args:
        issue: Finding to acknowledge.
        tool: Canonical tool name.
        root: Invocation workspace root.
        cwd: Working directory for relative diagnostic paths.

    Returns:
        Single-occurrence entry, or None for paths outside the workspace or
        findings that exceed the baseline schema's bounds.
    """
    file = relative_file(issue.file, root=root, cwd=cwd)
    if file is None:
        return None
    message = issue.message.replace("\r\n", "\n")
    try:
        return BaselineEntry(
            tool=tool.lower(),
            file=file,
            code=issue.get_code(),
            message_hash=hashlib.sha256(message.encode("utf-8")).hexdigest(),
            count=1,
            line=max(0, issue.line),
        )
    except ValidationError:
        # Unrepresentable findings stay visible; never truncate their identity.
        return None


def baseline_incomplete_reason(result: ToolResult) -> str | None:
    """Explain why a producer cannot safely acknowledge its findings.

    Args:
        result: Producer's completed result.

    Returns:
        Incompleteness reason, or None for a complete verdict.
    """
    if result.skipped:
        return "tool was skipped"
    if result.no_files:
        return "no files were inspected"
    if result.timed_out:
        return "execution timed out"
    if result.execution_failed:
        return "tool execution failed"
    if result.residual_unknown:
        return "residual findings are unknown"
    if result.parse_failures_count:
        return f"report has {result.parse_failures_count} parse failure(s)"
    if result.inspected_files is not True:
        return "file inspection was not confirmed"
    if result.issues_count != len(result.issues or []):
        return "finding count does not match the parsed report"
    if not result.success:
        if not result.findings_complete:
            return "producer does not certify complete findings for baselining"
        if result.issues_count <= 0:
            return "failed verdict is not explained by findings"
    return None


def result_is_complete(result: ToolResult) -> bool:
    """Require affirmative inspection and coherent parsed findings.

    Args:
        result: Producer's completed result.

    Returns:
        Whether findings can be acknowledged without hiding execution errors.
    """
    return baseline_incomplete_reason(result) is None


def snapshot(results: Sequence[ToolResult], *, root: Path) -> FingerprintBaseline:
    """Count observed fingerprints, retaining a deterministic line hint.

    Args:
        results: Unsuppressed check results.
        root: Invocation workspace root.

    Returns:
        Baseline containing only explicitly inspected findings.
    """
    entries: dict[Fingerprint, BaselineEntry] = {}
    for result in results:
        if not result_is_complete(result):
            continue
        for issue in result.issues or []:
            entry = fingerprint(issue, tool=result.name, root=root, cwd=result.cwd)
            if entry is None:
                continue
            previous = entries.get(entry.key)
            if previous is not None:
                entry = entry.model_copy(
                    update={
                        "count": previous.count + 1,
                        "line": min(previous.line, entry.line),
                    },
                )
            entries[entry.key] = entry
    return FingerprintBaseline(
        version=1,
        created=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        entries=sorted(entries.values(), key=lambda entry: entry.key),
    )


def maintain_baseline(
    previous: FingerprintBaseline,
    current: FingerprintBaseline,
    *,
    results: Sequence[ToolResult],
    root: Path,
    prune: bool,
) -> FingerprintBaseline:
    """Merge observations while removing entries only over explicit coverage.

    Args:
        previous: Existing validated acknowledgements.
        current: Current unsuppressed snapshot.
        results: Results carrying producer-owned exact coverage.
        root: Invocation workspace root.
        prune: Remove fixed findings without acknowledging new ones.

    Returns:
        Merged baseline preserving unseen tools, files, and occurrence budgets.
    """
    coverage: dict[tuple[str, str], frozenset[str]] = {}
    for result in results:
        if not result_is_complete(result):
            continue
        for file in result.inspected_paths or ():
            relative = relative_file(file, root=root, cwd=result.cwd)
            if relative is not None:
                key = (result.name.lower(), relative)
                coverage[key] = (
                    coverage.get(key, frozenset()) | result.uninspected_codes
                )
    observed = {entry.key: entry for entry in current.entries}
    entries: dict[Fingerprint, BaselineEntry] = {}
    for entry in previous.entries:
        uncovered = coverage.get((entry.tool, entry.file))
        if uncovered is None or entry.code in uncovered:
            entries[entry.key] = entry
            continue
        present = observed.get(entry.key)
        if present is not None:
            entries[entry.key] = present.model_copy(
                update={"count": min(entry.count, present.count)},
            )
    if not prune:
        for entry in current.entries:
            old = entries.get(entry.key)
            if old is None or entry.count >= old.count:
                entries[entry.key] = entry
    return previous.model_copy(
        update={
            "entries": sorted(entries.values(), key=lambda entry: entry.key),
        },
    )


def filter_issues(
    issues: Sequence[FindingT],
    *,
    result: ToolResult,
    baseline: FingerprintBaseline,
    root: Path,
) -> tuple[list[FindingT], int]:
    """Spend each fingerprint's occurrence budget once per finding list.

    Args:
        issues: Detected or residual findings.
        result: Tool and working directory that produced the findings.
        baseline: Validated acknowledgement budgets.
        root: Invocation workspace root.

    Returns:
        Remaining findings and the number acknowledged.
    """
    budget = Counter({entry.key: entry.count for entry in baseline.entries})
    retained: list[FindingT] = []
    for issue in issues:
        entry = fingerprint(issue, tool=result.name, root=root, cwd=result.cwd)
        if entry is not None and budget[entry.key] > 0:
            budget[entry.key] -= 1
        else:
            retained.append(issue)
    return retained, len(issues) - len(retained)
