"""Rebuild one tool's result from a saved lint report, failing closed (#3497).

A ``--lint-report`` is PR-controlled data (#2571), so nothing in it may make
a tool look finished unless the evidence says so. Two rules follow.

**Malformed entries reject the report**, the same way a non-list ``issues``
or a non-integer ``line`` already does: ``success`` must be a real boolean,
``status`` a known value, every flag a real boolean when present (``null``
included), ``issues`` a list, and ``issues_count`` must match it. The review
then runs from the diff alone with a header note naming the problem.

**Contradictory evidence makes the entry incomplete.** Incomplete evidence
in either the flags or ``status`` wins over a clean, skipped or unavailable
claim. An entry whose claims disagree (a skip beside a failure status, a
clean status beside a failed verdict, a failure status with no findings)
is reported as an execution failure naming the contradiction, so its warning
reaches the prompt instead of a clean verdict.

**A fix report's verdict is its ``remaining`` count**, read by one closed
rule. The writer emits ``remaining``, ``fixed`` and ``net_resolved`` together
on every fix row and never on a check row, either all ``null`` (unmeasured)
or all integers (unmeasured counts default to 0), with ``fixed`` an alias of
``net_resolved``. Its ``issues`` are the findings detected before the fix
merged with the ones left after it, with nothing marking which is which.

1. A document whose ``action`` is not ``fix`` may not carry fix accounting;
   the three keys come all together or not at all; integers must satisfy
   ``fixed == net_resolved``, ``0 <= remaining <= len(issues)`` and
   ``0 <= fixed + remaining <= len(issues)`` (the pre-fix count cannot
   exceed the merged list). Anything else rejects the report.
2. Incomplete evidence wins first, exactly as for a check row: ``remaining``
   never overrides a flag, a status or an unexplained failure.
3. Then: nothing remaining is clean and shows no findings; every listed
   finding remaining shows them all; some remaining shows none, with a note
   giving the count; ``null`` counts need a residual reason; integer counts
   that account for none of the listed findings are "no measured counts".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from lintro.models.core.tool_outcome import ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue

__all__ = [
    "REPORT_FIX_FILES_METADATA_KEY",
    "REPORT_NOTE_METADATA_KEY",
    "report_tool_result",
]

#: ``ToolResult.metadata`` key for the files a fix row's unlisted remaining
#: findings may be on: the files of every finding it listed. Restriction to
#: the changed files drops the note when none of them is under review.
REPORT_FIX_FILES_METADATA_KEY: Final[str] = "lint_report_fix_files"

#: The fix accounting keys the writer emits together on every fix row.
_FIX_KEYS: Final[tuple[str, ...]] = ("remaining", "fixed", "net_resolved")

#: ``ToolResult.metadata`` key for a note the digest prints for a tool whose
#: report entry is complete but whose findings cannot be listed.
REPORT_NOTE_METADATA_KEY: Final[str] = "lint_report_note"

#: Boolean markers the report writer emits; absent means false.
_FLAGS: Final[tuple[str, ...]] = (
    "execution_failed",
    "timed_out",
    "residual_unknown",
    "skipped",
    "unavailable",
)

#: ``status`` values, from the report writer (``ToolResultStatus``) and the
#: run-status vocabulary (``ToolRunStatus``), by what they claim.
_CLEAN: Final[frozenset[str]] = frozenset({"ok", "passed"})
_FINDINGS: Final[frozenset[str]] = frozenset({"failed", "issues"})
_INCOMPLETE: Final[frozenset[str]] = frozenset({"errored", "timed_out", "unknown"})
_SKIP: Final[frozenset[str]] = frozenset({"skipped"})
_UNAVAILABLE: Final[frozenset[str]] = frozenset({"unavailable"})
_STATUSES: Final[frozenset[str]] = (
    _CLEAN | _FINDINGS | _INCOMPLETE | _SKIP | _UNAVAILABLE
)

#: Residual reason used when the report names none.
_RESIDUAL_FALLBACK: Final[str] = "remaining issues unknown"


def _typed(entry: Mapping[str, Any], key: str, kind: type) -> Any:
    """Read an optional field that, when present, must have type *kind*.

    Args:
        entry: One per-tool report object.
        key: Field name.
        kind: Required type; ``bool`` is never accepted as an ``int``.

    Returns:
        The value, or None when the key is absent.

    Raises:
        ValueError: When the key is present with another type, ``null``
            included.
    """
    if key not in entry:
        return None
    value = entry[key]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        msg = f"has a non-{kind.__name__} {key!r}"
        raise ValueError(msg)
    return value


def _text(entry: Mapping[str, Any], key: str) -> str | None:
    """Read an optional text field, which the writer may write as ``null``.

    Args:
        entry: One per-tool report object.
        key: Field name.

    Returns:
        The text, or None when absent or ``null``.
    """
    if entry.get(key) is None:
        return None
    text: str = _typed(entry, key, str)
    return text


def _count(entry: Mapping[str, Any], key: str) -> int | None:
    """Read an optional non-negative count, which may be written as ``null``.

    Args:
        entry: One per-tool report object.
        key: Field name.

    Returns:
        The count, or None when absent or ``null``.

    Raises:
        ValueError: When the count is mistyped or negative.
    """
    if entry.get(key) is None:
        return None
    count: int = _typed(entry, key, int)
    if count < 0:
        msg = f"has a negative {key!r}"
        raise ValueError(msg)
    return count


def _validated(
    entry: Mapping[str, Any],
    issues: Sequence[BaseIssue],
) -> tuple[bool, str | None, dict[str, bool]]:
    """Validate the verdict fields of a report entry.

    Args:
        entry: One per-tool report object.
        issues: Its parsed findings.

    Returns:
        ``success``, ``status`` (None when absent) and the flags.

    Raises:
        ValueError: When a verdict field is missing, mistyped or inconsistent
            with the findings listed.
    """
    if "success" not in entry:
        msg = "has no 'success' verdict"
        raise ValueError(msg)
    success: bool = _typed(entry, "success", bool)
    status: str | None = _typed(entry, "status", str)
    if status is not None and status not in _STATUSES:
        msg = f"has an unknown 'status' {status!r}"
        raise ValueError(msg)
    flags = {key: bool(_typed(entry, key, bool)) for key in _FLAGS}
    count: int | None = _typed(entry, "issues_count", int)
    if count is not None and count < 0:
        msg = "has a negative 'issues_count'"
        raise ValueError(msg)
    if count is not None and count != len(issues):
        msg = f"has 'issues_count' {count} but lists {len(issues)} issue(s)"
        raise ValueError(msg)
    return success, status, flags


def _fix_accounting(
    entry: Mapping[str, Any],
    *,
    fix_document: bool | None,
    listed: int,
) -> tuple[bool, int | None, int | None]:
    """Read and validate a fix row's accounting by the module's closed rule.

    Args:
        entry: One per-tool report object.
        fix_document: Whether the document's ``action`` is ``fix``; None
            when the document does not say (the stdout writer omits it).
        listed: How many issues the entry lists.

    Returns:
        Whether the entry is a fix row, its ``remaining`` and its ``fixed``
        count (both None when the row says they were not measured).

    Raises:
        ValueError: When the accounting breaks the rule; the caller rejects
            the report.
    """
    present = [key for key in _FIX_KEYS if key in entry]
    if not present:
        if fix_document:
            msg = "has no fix accounting in a fix report"
            raise ValueError(msg)
        return False, None, None
    if fix_document is False:
        msg = f"carries fix accounting ({', '.join(present)}) in a check report"
        raise ValueError(msg)
    if len(present) != len(_FIX_KEYS):
        msg = "carries partial fix accounting (needs remaining, fixed, net_resolved)"
        raise ValueError(msg)
    if all(entry[key] is None for key in _FIX_KEYS):
        return True, None, None
    remaining, fixed, net = (_typed(entry, key, int) for key in _FIX_KEYS)
    if remaining is None or fixed is None or net is None:
        msg = "mixes null and measured fix accounting"
        raise ValueError(msg)
    if fixed != net:
        msg = f"has 'fixed' {fixed} but 'net_resolved' {net}"
        raise ValueError(msg)
    if not 0 <= remaining <= listed or not 0 <= fixed + remaining <= listed:
        msg = (
            f"has fix counts (remaining {remaining}, fixed {fixed}) that do "
            f"not fit its {listed} listed issue(s)"
        )
        raise ValueError(msg)
    return True, remaining, fixed


def _contradiction(
    *,
    success: bool,
    status: str | None,
    flags: Mapping[str, bool],
    incomplete: bool,
    has_issues: bool,
) -> str | None:
    """Name how an entry's claims disagree, or None when they agree.

    Args:
        success: The entry's verdict.
        status: Its ``status``, or None when absent.
        flags: Its boolean markers.
        incomplete: Whether any incomplete evidence is present.
        has_issues: Whether it lists findings.

    Returns:
        A short description of the contradiction, or None.
    """
    skip = flags["skipped"] or status in _SKIP
    unavailable = flags["unavailable"] or status in _UNAVAILABLE
    if skip and unavailable:
        return "claims to be both skipped and unavailable"
    if skip or unavailable:
        claimed = "skipped" if skip else "unavailable"
        if incomplete or has_issues:
            return f"claims to be {claimed} but carries a failure or findings"
        if status not in (None, claimed) or not flags[claimed]:
            return f"claims to be {claimed} without consistent status evidence"
        if skip and not success:
            return "claims to be skipped but reports failure"
        return None
    if status in _CLEAN and (not success or has_issues):
        return f"has status {status!r} but reports failure or findings"
    if status in _FINDINGS and success and not has_issues:
        return f"has status {status!r} but reports success and no findings"
    return None


def _lead(name: str, *, problem: str | None, status: str | None) -> str | None:
    """Name why a report entry is no verdict its other fields would carry.

    Args:
        name: The tool's name.
        problem: The entry's contradiction, if any.
        status: Its ``status``.

    Returns:
        The reason the entry is an execution failure, or None.
    """
    if problem:
        return f"report entry for {name} {problem}"
    if status in ("errored", "unknown"):
        return f"report entry for {name} has status {status!r}"
    return None


def report_tool_result(
    entry: Mapping[str, Any],
    *,
    name: str,
    issues: Sequence[BaseIssue],
    fix_document: bool | None = None,
) -> ToolResult:
    """Rebuild one tool's result from a saved report entry.

    Args:
        entry: One per-tool report object.
        name: The tool's name.
        issues: Its parsed findings.
        fix_document: Whether the document's ``action`` is ``fix``; None
            when the document does not say.

    Returns:
        The rebuilt result: complete, skipped or unavailable only when every
        piece of evidence agrees, and otherwise incomplete.
    """
    success, status, flags = _validated(entry, issues)
    fix_row, remaining, fixed = _fix_accounting(
        entry,
        fix_document=fix_document,
        listed=len(issues),
    )
    if fix_row:
        # The listed findings are fix history; only the count is a verdict.
        listed = issues if remaining == len(issues) else ()
        found = bool(remaining)
    else:
        listed, found = issues, bool(issues)
    parse_failures = _count(entry, "parse_failures_count")
    output = _text(entry, "output")
    timed_out = flags["timed_out"] or status == "timed_out"
    incomplete = bool(
        flags["execution_failed"]
        or timed_out
        or flags["residual_unknown"]
        or parse_failures
        or status in _INCOMPLETE,
    )
    problem = _contradiction(
        success=success,
        status=status,
        flags=flags,
        incomplete=incomplete,
        has_issues=found,
    )
    if (
        problem is None
        and fix_row
        and remaining is None
        and not (incomplete or flags["unavailable"])
    ):
        problem = "is a fix report with an unknown residual but no reason"
    if (
        problem is None
        and fix_row
        and issues
        and remaining == 0
        and fixed == 0
        and not incomplete
    ):
        # The writer defaults unmeasured counts to 0, so counts that account
        # for none of the listed findings measured nothing.
        problem = (
            f"is a fix report with no measured counts for its {len(issues)} "
            "listed issue(s)"
        )
    if problem is None and flags["unavailable"]:
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Unavailable(
                    reason=_text(entry, "unavailable_reason") or "tool not found",
                    strict=not success,
                ),
            ),
            name=name,
            output=output,
        )
    if problem is None and flags["skipped"]:
        return ToolResult(
            name=name,
            success=True,
            skipped=True,
            skip_reason=_text(entry, "skip_reason") or "skipped",
            output=output,
        )
    lead = _lead(name, problem=problem, status=status)
    unlisted = bool(fix_row and remaining and not listed)
    return ToolResult(
        name=name,
        success=success and not incomplete and problem is None,
        issues_count=remaining if unlisted and remaining else len(listed),
        issues=list(listed),
        metadata=(
            {
                REPORT_NOTE_METADATA_KEY: (
                    f"fix report: {remaining} issue(s) remain after the fix; "
                    "the report does not say which"
                ),
                REPORT_FIX_FILES_METADATA_KEY: sorted(
                    {str(getattr(issue, "file", "") or "") for issue in issues},
                ),
            }
            if unlisted and lead is None and not incomplete
            else {}
        ),
        output=f"{lead}\n{output}" if lead and output else lead or output,
        execution_failed=flags["execution_failed"] or lead is not None,
        timed_out=timed_out,
        residual_unknown=flags["residual_unknown"],
        residual_unknown_reason=(
            (_text(entry, "residual_unknown_reason") or _RESIDUAL_FALLBACK)
            if flags["residual_unknown"]
            else None
        ),
        parse_failures_count=parse_failures,
    )
