"""Record files as checked only after the tool has run (#3578).

Under ``--incremental``, discovery reads a tool's cache to choose the files it
hands the tool and fingerprints those files. It writes nothing. The executor
calls :func:`finish_incremental_run` once it holds the tool's result, and only
then are files recorded, and only the ones the run proved clean:

* only a ``check`` run records; a ``format`` run's verdict is settled later by
  the verify pass, so it records nothing;
* only discovered paths that the code which ran the tool stated it handed
  to a completed invocation (``BaseToolPlugin._record_checked``) can be
  recorded, under their exact discovery key: a plugin that states nothing
  records nothing;
* nothing is recorded unless the result is complete: the tool ran and was not
  unavailable, refused, skipped, timed out or failed, and its verdict is
  either clean or carries findings from a report parsed in full
  (``findings_complete``);
* a file with a finding is not recorded, so the next incremental run checks it
  again and reports the finding again;
* a finding that cannot be tied to a handed file records nothing.

A run that raises, is interrupted or is killed never reaches this point, so it
records nothing either.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from lintro.utils.file_cache import FingerprintSnapshot, ToolCache
from lintro.utils.refused_command import command_refusal

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.file_cache import FileFingerprint


@dataclass(frozen=True)
class PendingCheck:
    """Files one discovery handed a tool under ``--incremental``.

    Attributes:
        tool_name: Name the tool's cache is kept under.
        cwd: Directory the tool runs in; relative finding paths resolve here.
        snapshot: Fingerprints of the handed files, taken at discovery.
    """

    tool_name: str
    cwd: str
    snapshot: FingerprintSnapshot


def incremental_note(tool_name: str) -> str:
    """Explain why ``--incremental`` did not narrow a tool.

    Args:
        tool_name: Tool whose definition sets ``incremental_cacheable=False``.

    Returns:
        One sentence for the tool's output.
    """
    return (
        f"--incremental does not apply to {tool_name}: it checks every file in "
        "scope on each run, because its result depends on data that changes "
        "independently of the files."
    )


def _complete(result: ToolResult) -> bool:
    """Report whether a result proves its run checked every handed file.

    A clean verdict (success, no findings) counts only with affirmative
    inspection evidence (``inspected_files=True``): success alone does not
    prove a file was read, as when part of a check was skipped. A verdict with
    findings proves the other files clean only when the producer certifies
    that its report was parsed in full (``findings_complete``): a parser may
    drop a malformed record, and with it a finding in another file.

    Args:
        result: The tool's result, before any display filtering.

    Returns:
        True only for a finished run with a recognised verdict.
    """
    if (
        result.execution_failed
        or result.timed_out
        or result.skipped
        or result.unavailable
        or result.residual_unknown
        or result.no_files
        or result.parse_failures_count
        or result.inspected_files is False
        or result.issues_count != len(result.issues)
    ):
        return False
    if not result.issues:
        return result.success and result.inspected_files is True
    return result.findings_complete


def checked_files(
    pending: PendingCheck,
    result: ToolResult,
    stated: set[str],
) -> dict[str, FileFingerprint]:
    """Return the handed files the run checked to completion with no finding.

    Args:
        pending: What discovery handed the tool.
        result: The tool's result.
        stated: Discovered paths stated as handed to a completed invocation.

    Returns:
        Discovery-time fingerprints of the files to record, by path. Empty
        when the result is incomplete or a finding names no handed file.
    """
    if not _complete(result):
        return {}
    # Paths are compared as written (made absolute against the run's
    # directory), never resolved: a symlink and its target are two entries,
    # each recorded only if it was itself handed and checked.
    handed = pending.snapshot.fingerprints
    flagged: set[str] = set()
    for issue in result.issues:
        name = os.path.normpath(os.path.join(pending.cwd, issue.file or ""))
        if not issue.file or name not in handed:
            return {}
        flagged.add(name)
    inspected = (
        None
        if result.inspected_paths is None
        else {
            os.path.normpath(os.path.join(pending.cwd, path))
            for path in result.inspected_paths
        }
    )
    return {
        path: fingerprint
        for path, fingerprint in handed.items()
        if path in stated
        and path not in flagged
        and (inspected is None or path in inspected)
    }


def _merged(pending: list[PendingCheck]) -> PendingCheck | None:
    """Merge every discovery of one run into one pending check.

    A plugin may prepare more than once in a run (a fix retry, or ruff's lint
    and format halves). The first fingerprint of a file wins, so a file the
    run itself rewrote no longer matches and is checked again next time. A
    file any discovery could not read is never recorded.

    Args:
        pending: Discoveries in the order they happened.

    Returns:
        The merged check, or None when the discoveries ran in different
        directories and relative finding paths would be ambiguous.
    """
    if len({check.cwd for check in pending}) != 1:
        return None
    fingerprints: dict[str, FileFingerprint] = {}
    for check in reversed(pending):
        fingerprints.update(check.snapshot.fingerprints)
    unreadable = {path for check in pending for path in check.snapshot.unreadable}
    for path in unreadable:
        fingerprints.pop(path, None)
    return PendingCheck(
        tool_name=pending[0].tool_name,
        cwd=pending[0].cwd,
        snapshot=FingerprintSnapshot(fingerprints, tuple(sorted(unreadable))),
    )


def _pending(tool: object) -> list[PendingCheck]:
    """Take the discoveries a tool instance holds, leaving it none.

    Args:
        tool: The plugin instance that ran; any object is accepted.

    Returns:
        The pending checks, oldest first; empty for an object that holds none.
    """
    pending = getattr(tool, "incremental_pending", None)
    if not isinstance(pending, list) or not pending:
        return []
    taken = [check for check in pending if isinstance(check, PendingCheck)]
    pending.clear()
    return taken


def _stated(tool: object) -> set[str]:
    """Take the paths a tool instance stated it handed and checked.

    Args:
        tool: The plugin instance that ran; any object is accepted.

    Returns:
        Real paths; empty for an object that tracks none.
    """
    stated = getattr(tool, "incremental_checked", None)
    if not isinstance(stated, set):
        return set()
    taken = {path for path in stated if isinstance(path, str)}
    stated.clear()
    return taken


def _record(pending: list[PendingCheck], checked: dict[str, FileFingerprint]) -> None:
    """Save the files a run proved clean and forget the rest it was handed.

    Args:
        pending: Every discovery of the run.
        checked: Discovery-time fingerprints of the files to record.
    """
    cache = ToolCache.load(pending[0].tool_name)
    cache.record(
        checked,
        unchecked=[
            path
            for check in pending
            for path in (*check.snapshot.fingerprints, *check.snapshot.unreadable)
            if path not in checked
        ],
    )
    cache.save()


def finish_incremental_run(
    tool: object,
    result: ToolResult,
    *,
    record: bool,
) -> ToolResult:
    """Record what the tool's run proved clean, after the run.

    Every executor calls this with the tool instance that ran and its result,
    before the result is filtered for display. Files the tool was handed and
    did not prove clean are dropped from its cache.

    Args:
        tool: The plugin instance that ran.
        result: Its result.
        record: Whether the run was a check. Any other run leaves the cache
            as it was.

    Returns:
        The result, with the ``--incremental`` note added for a tool that
        declares ``incremental_cacheable=False``.
    """
    pending, stated = _pending(tool), _stated(tool)
    if pending and record:
        merged = _merged(pending)
        refused = command_refusal(tool) is not None
        _record(
            pending,
            (
                {}
                if merged is None or refused
                else checked_files(merged, result, stated)
            ),
        )
    definition = getattr(tool, "definition", None)
    options = getattr(tool, "options", None)
    if (
        getattr(definition, "incremental_cacheable", True) is not False
        or not isinstance(options, dict)
        or options.get("incremental") is not True
        or result.skipped
        or result.unavailable
    ):
        return result
    note = incremental_note(str(getattr(definition, "name", result.name)))
    output = f"{result.output}\n\n{note}" if result.output else note
    return replace(result, output=output)
