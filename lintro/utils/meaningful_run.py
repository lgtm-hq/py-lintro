"""Decide whether a run measured enough to be a comparable quality signal.

Two consumers ask the same question in slightly different ways, and both got
it wrong independently before this module existed (issue #1739):

* ``lintro badge`` must not publish a public "0 issues" badge for a directory
  nothing inspected, or for a run whose tools timed out.
* The severity baseline must not record — or compare against — a run that did
  not measure the same population as a normal ``check``.

A tool that finds nothing to do often still returns ``skipped=False`` and
``success=True``, so its message is the only signal that nothing was looked
at. That covers two shapes: "No <files> to check" for an empty file set, and
"Skipping <tool>: ..." for a wrapper that declined to run at all (``vale`` and
``stylelint`` use the latter without setting ``skipped``, unlike ``spectral``
and ``commitlint``). :func:`result_inspected_files` classifies both.

Known limitation
----------------
The classification is a heuristic over the result's text, and one shape is
genuinely ambiguous: an **empty** ``output`` means "clean pass" for some
wrappers (``ruff``, ``pydoclint``, ``semgrep``, ``gitleaks`` all return no
output when they inspected files and found nothing) and "nothing to do" for
others (``bandit`` deliberately nulls its output for the no-files case, see
``bandit.py``). Empty output is therefore treated as *inspected*, because the
opposite would make every clean run look unmeasured. Issue #2369 removes the
ambiguity by giving no-work results a structured signal instead of a message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.action import Action

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.tool_result import ToolResult

__all__ = [
    "baseline_is_eligible",
    "result_inspected_files",
    "run_inspected_files",
]

#: Endings of the "nothing to do" messages wrappers emit, checked after the
#: trailing period is stripped. Deliberately specific: a message like "No
#: issues found" or "No typos found." means the tool *did* inspect files and
#: found nothing, so a bare "found" test would misclassify a clean run.
_NO_WORK_ENDINGS: tuple[str, ...] = (
    "to check",
    "to fix",
    "to format",
    "to lint",
    "files found",
    "include paths",
)

#: Substring marking a wrapper that bailed out because a prerequisite manifest
#: was absent, e.g. "No Cargo.lock found; skipping cargo-audit.".
_SKIPPING_MARKER = "; skipping"

#: Prefix of the whole-output message a wrapper emits when it declines to run
#: at all — missing configuration, a failed auto-install, an unusable version.
#: ``vale`` and ``stylelint`` use this shape *without* setting ``skipped=True``,
#: so it is the only signal that they inspected nothing.
_SKIPPING_PREFIX = "skipping "


def _reports_no_work(text: str) -> bool:
    """Return whether a result's text says the tool found nothing to do.

    Args:
        text: The result's combined ``output`` and ``formatted_output``.

    Returns:
        bool: ``True`` when the message names an empty file/path set, or says
        the tool declined to run, rather than a clean inspection.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    # Matched on the first line only: a "Skipping <tool>: ..." result carries
    # that message as its entire output, whereas a tool that really ran could
    # mention "skipping" somewhere inside a findings blob.
    if lines[0].lower().startswith(_SKIPPING_PREFIX):
        return True

    for line in lines:
        stripped = line.rstrip(".").lower()
        if not stripped.startswith("no "):
            continue
        if _SKIPPING_MARKER in stripped or stripped.endswith(_NO_WORK_ENDINGS):
            return True
    return False


def result_inspected_files(result: ToolResult) -> bool:
    """Return whether a tool result looks like it actually inspected files.

    Both ``output`` and ``formatted_output`` are searched, because some
    wrappers put the message in only one of them.

    Args:
        result: One tool's completed result.

    Returns:
        bool: ``True`` when the tool was not skipped, did not time out, and
        did not report an empty file or path set. See the module docstring for
        why empty output counts as inspected.
    """
    if result.skipped or result.timed_out:
        return False
    text = f"{result.output or ''}\n{result.formatted_output or ''}"
    return not _reports_no_work(text)


def run_inspected_files(results: Sequence[ToolResult]) -> bool:
    """Return whether any tool in a run actually inspected files.

    Args:
        results: Every result the run produced, skipped placeholders included.

    Returns:
        bool: ``True`` when at least one result inspected files.
    """
    return any(result_inspected_files(result) for result in results)


def baseline_is_eligible(
    *,
    action: Action,
    dry_run_preview: bool,
    tool_results: Sequence[ToolResult],
    early_exit: bool = False,
) -> bool:
    """Return whether a run may read or write the severity baseline.

    The same predicate gates both sides on purpose: a run that must not
    *record* a baseline must not *compare against* one either, or it reports a
    delta between two different populations.

    A run qualifies only when all of the following hold:

    * It is a ``check``. ``fmt`` and ``test`` measure something else.
    * It is not a ``fmt --dry-run`` preview. Those report ``CHECK`` here
      because they execute read-only, but their results are filtered to the
      auto-fixable subset, so their counts are a strict undercount.
    * At least one tool actually inspected files. An all-skipped run still
      carries ``skipped=True`` placeholder results, so a non-empty result list
      is not evidence that anything was measured; recording its zero counts
      would make the next real check report every existing issue as newly
      introduced.
    * It did not exit before running anything.

    Args:
        action: The action the run executed.
        dry_run_preview: Whether this run is a ``fmt --dry-run`` preview.
        tool_results: Every result the run produced.
        early_exit: Whether the run stopped before executing any tool.

    Returns:
        bool: ``True`` when the run is a comparable measurement.
    """
    if early_exit or dry_run_preview or action != Action.CHECK:
        return False
    return run_inspected_files(tool_results)
