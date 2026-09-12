"""The verify half of the mutate-then-verify ``fmt`` pipeline (#1743).

``lintro fmt`` runs the mutating capabilities (``FIX``, ``FORMAT``) in derived
DAG order and then makes **one** verify pass with the ``CHECK`` capabilities of
the same tools. That single pass is the authoritative residual count, replacing
the per-plugin "lint again after formatting" implementations each mutating tool
used to carry privately. Only a run-level pass can see cross-tool interference:
if ruff fixes a file and prettier then reformats it, ruff's own post-fix lint
already ran, and the new scheduler deliberately sequences more mutating tools
over the same files.

Scope narrowing
---------------
Verifying every file again would double the cost of a format run, so the pass
is narrowed with :class:`~lintro.utils.file_cache.FingerprintSnapshot`: every
file a mutating capability could be handed is stat'ed before the mutation
phase and re-stat'ed after it, and only the files whose fingerprint moved are
verified.

mtime **over-approximates**. A formatter that rewrites a file to byte-identical
content still bumps mtime, so a file that did not need re-verifying may be
re-verified anyway. That is a wasted check, not a wrong answer, and the
dangerous direction — a mutated file the pass skips — cannot occur while
fingerprints are trustworthy. Size alone is near-useless (a quote-style rewrite
is byte-for-byte the same length) and serves only as a cheap tiebreak.

The floor
---------
When fingerprints are unavailable or unreliable — a stat that fails, or a
filesystem with whole-second mtime granularity where a rewrite inside the same
second is invisible — the pass degrades to **every file handed to a mutating
capability**. That is the documented floor, not a separate implementation: the
same verify pass runs, over a wider file set. "Handed to" is literal: the
candidate set is built per tool and carries the run's ``--incremental`` and
``--diff`` scoping, so the floor can never re-check a file this run could not
have touched.

Residual accounting
-------------------
A file whose fingerprint did *not* move was not rewritten, so the issues it
had before the mutation phase are exactly the issues it has after it. The
authoritative residual is therefore the verify pass's findings on the changed
files plus the mutation phase's pre-fix findings on the unchanged ones, and
``fixed`` is derived from it rather than self-reported. Nothing is counted
twice: a tool's own post-fix opinion is discarded, not added.

Where this lives
----------------
The pass reads tool claims, configures plugins and normalises issue paths, so
it sits in the ``tools`` layer next to
:mod:`lintro.tools.core.scheduler` — ``lintro.utils`` may not import
``lintro.tools``, ``lintro.plugins`` or ``lintro.parsers``. The half that is
pure path and fingerprint arithmetic stays below it, in
:mod:`lintro.utils.file_cache` and :mod:`lintro.utils.path_filtering`.
:mod:`lintro.utils.tool_executor` drives the pipeline and reaches this module
through the ``lintro.tools`` package re-export, which is the one edge the
layering baseline already records for it.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from loguru import logger

from lintro.enums.action import Action
from lintro.enums.capability import MUTATING_CAPABILITIES, Cap
from lintro.enums.verify_status import VerifyStatus
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.file_cache import FingerprintSnapshot, snapshot_fingerprints
from lintro.utils.path_filtering import (
    setup_exclude_patterns,
    walk_files_with_excludes,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from lintro.models.core.claim import Claim

__all__ = [
    "COARSE_MTIME_REASON",
    "CRASHED_REASON",
    "RESIDUAL_UNKNOWN_TEMPLATE",
    "SKIPPED_REASON",
    "TIMED_OUT_REASON",
    "UNRESOLVABLE_REASON",
    "UnresolvableToolError",
    "NARROWED_REASON",
    "UNREADABLE_REASON",
    "VERIFY_NOTE_TEMPLATE",
    "VerifiableTool",
    "VerifyBaseline",
    "VerifyOutcome",
    "VerifyScope",
    "VerifyStatus",
    "capture_verify_baseline",
    "fold_verify_results",
    "resolve_result_capability",
    "resolve_verify_scope",
    "run_verify_pass",
    "verifying_tools",
]

#: Reason recorded when fingerprints narrowed the scope successfully.
NARROWED_REASON: str = ""

#: Floor reason: the filesystem cannot distinguish a rewrite inside one second.
COARSE_MTIME_REASON: str = "coarse mtime resolution"

#: Floor reason: at least one candidate file could not be stat'ed.
UNREADABLE_REASON: str = "some files could not be fingerprinted"

#: Why a residual is unknown: the ``CHECK`` raised before it could answer.
CRASHED_REASON: str = "the verify check could not run ({error})"

#: Why a residual is unknown: the ``CHECK`` burned its deadline. A partial
#: answer over part of the scope is not an answer over the scope.
TIMED_OUT_REASON: str = "the verify check timed out"

#: Why a residual is unknown: the ``CHECK`` returned without executing, a
#: version gate being the usual cause.
SKIPPED_REASON: str = "the verify check was skipped before it ran"

#: Why a residual is unknown: the registry could not resolve the tool, so
#: nothing is known about it — including its own ``remaining`` count.
UNRESOLVABLE_REASON: str = "the tool could not be resolved"

#: Note appended to a tool's output when its residual could not be measured.
#: Displayed instead of an after-count, never beside one.
RESIDUAL_UNKNOWN_TEMPLATE: str = (
    "Verify pass: residual unknown — {reason}. The {detected} issue(s) "
    "detected before the mutation phase are reported as-is; this run fails "
    "because the count after it was never measured."
)

#: Note appended to a tool's output when the verify pass and the fix pass
#: disagree about the residual. Lifted out of ``_fold_one`` so the tests and
#: ``docs/configuration.md`` pin the same string the run emits.
VERIFY_NOTE_TEMPLATE: str = (
    "Verify pass: {residual} issue(s) remain after all mutating tools ran "
    "(the fix pass reported {previous})."
)


class UnresolvableToolError(LookupError):
    """Raised when the registry cannot resolve a tool the run selected.

    Distinguishing this from "declares no claims" is what stops the verify
    pass failing open: prettier legitimately declares no ``CHECK``, and its
    mutation result stands. A name that cannot be resolved is a different
    thing — nothing is known about it, so its self-reported ``remaining=0``
    must not be trusted either.
    """


class VerifiableTool(Protocol):
    """The one thing the verify pass needs a configured plugin to do.

    Structural rather than nominal on purpose: ``lintro.tools`` and
    ``lintro.plugins`` are sibling layers, so this module may not name
    ``BaseToolPlugin``. It only ever calls ``check``, which makes the narrow
    protocol the honest signature anyway.
    """

    def check(
        self,
        paths: list[str],
        options: dict[str, object],
    ) -> ToolResult:
        """Report diagnostics for the given paths without modifying them.

        Args:
            paths: Files or directories to check.
            options: Runtime options for this invocation.

        Returns:
            ToolResult: The tool's check-mode result.
        """
        ...  # pragma: no cover - protocol declaration


def _claims_for(tool_name: str) -> list[Claim]:
    """Read a tool's declared claims, tolerating an unresolvable name.

    Args:
        tool_name: Registry key of the tool.

    Returns:
        The tool's claims, or an empty list when it declares none.

    Raises:
        UnresolvableToolError: If the registry cannot resolve the name.
    """
    # Imported here rather than at module scope: ``lintro.tools.__init__``
    # re-exports this module, so a top-level import would close a cycle. The
    # scheduler resolves its own claims the same way.
    from lintro.tools import tool_manager

    try:
        definition = tool_manager.get_tool(tool_name).definition
    except (AttributeError, KeyError, ValueError, RuntimeError) as exc:
        raise UnresolvableToolError(tool_name) from exc
    return list(getattr(definition, "claims", None) or ())


def _claims_or_none(tool_name: str) -> list[Claim] | None:
    """Read a tool's claims, reporting an unresolvable name as ``None``.

    Args:
        tool_name: Registry key of the tool.

    Returns:
        The tool's claims, or ``None`` when the registry cannot resolve it.
    """
    try:
        return _claims_for(tool_name)
    except UnresolvableToolError:
        logger.debug(f"Verify pass cannot resolve tool {tool_name!r}")
        return None


def resolve_result_capability(*, tool_name: str, action: Action) -> Cap | None:
    """Return the capability a tool's result represents for one action.

    Args:
        tool_name: Registry key of the tool.
        action: The action the executor ran.

    Returns:
        ``Cap.CHECK`` outside a fix run. Inside one, the earliest mutating
        capability the tool declares (``FIX`` before ``FORMAT``, matching the
        scheduler's phase order), or ``None`` when the tool declares no
        mutating capability at all.
    """
    if action != Action.FIX:
        return Cap.CHECK
    declared: set[Cap] = set()
    for claim in _claims_or_none(tool_name) or ():
        declared |= claim.capabilities & MUTATING_CAPABILITIES
    if Cap.FIX in declared:
        return Cap.FIX
    if Cap.FORMAT in declared:
        return Cap.FORMAT
    return None


def _mutating_patterns_by_tool(
    tool_names: Sequence[str],
) -> dict[str, list[str]]:
    """Map each selected tool to the file patterns it may rewrite.

    Args:
        tool_names: Tools selected for the run.

    Returns:
        Sorted, de-duplicated glob patterns per tool, omitting tools that
        rewrite nothing addressed by pattern. A project-scoped claim (no
        patterns) contributes nothing, because it is not addressed by pattern.
    """
    by_tool: dict[str, list[str]] = {}
    for name in tool_names:
        patterns: set[str] = set()
        for claim in _claims_or_none(name) or ():
            if claim.is_mutating:
                patterns.update(claim.patterns)
        if patterns:
            by_tool[name] = sorted(patterns)
    return by_tool


def verifying_tools(tool_names: Sequence[str]) -> list[str]:
    """Return the selected tools that declare a ``CHECK`` capability.

    Args:
        tool_names: Tools selected for the run, in execution order.

    Returns:
        The subset that can verify, in the same order. A tool with no
        ``CHECK`` claim (prettier is ``FORMAT``-only) has no residual to
        report and is not asked for one.
    """
    return [name for name, resolvable in _verify_targets(tool_names) if resolvable]


def _verify_targets(tool_names: Sequence[str]) -> list[tuple[str, bool]]:
    """Return every tool the verify pass must account for, and whether it resolved.

    Two different things used to collapse onto "no claims": prettier
    legitimately declares no ``CHECK`` and its mutation result stands, while a
    name the registry cannot resolve is a tool nothing is known about. Folding
    the second like the first would trust its self-reported ``remaining=0``,
    which is the fail-open this pass exists to close.

    Args:
        tool_names: Tools selected for the run, in execution order.

    Returns:
        ``(name, resolvable)`` pairs, in order. Resolvable tools appear only
        when they declare ``CHECK``; unresolvable ones always appear, so the
        pass can report that it could not verify them.
    """
    targets: list[tuple[str, bool]] = []
    for name in tool_names:
        claims = _claims_or_none(name)
        if claims is None:
            targets.append((name, False))
            continue
        if any(Cap.CHECK in claim.capabilities for claim in claims):
            targets.append((name, True))
    return targets


@dataclass(frozen=True)
class VerifyOutcome:
    """What the verify pass could say about one tool.

    Three states, not two. ``VERIFIED`` and ``UNCHANGED`` are both verdicts:
    the residual was measured, or nothing needed measuring because nothing was
    rewritten. ``UNKNOWN`` is the absence of a verdict, and the fold keeps it
    that way — the tool's residual is reported as unknown rather than as a
    number nobody measured.

    Attributes:
        tool: The verifying tool's registry name.
        result: Its ``CHECK`` result, or ``None`` when no check answered.
        status: Which of the three outcomes this is.
        unknown_reason: Why the residual could not be measured. Set on
            ``UNKNOWN`` and rendered next to the tool, empty otherwise.
    """

    tool: str
    result: ToolResult | None
    status: VerifyStatus = VerifyStatus.VERIFIED
    unknown_reason: str = ""

    def __post_init__(self) -> None:
        """Validate that an unknown outcome carries its reason.

        Raises:
            ValueError: If the status and the reason disagree.
        """
        if self.status is VerifyStatus.UNKNOWN and not self.unknown_reason:
            raise ValueError("unknown_reason is required when status is UNKNOWN")
        if self.unknown_reason and self.status is not VerifyStatus.UNKNOWN:
            raise ValueError("unknown_reason is only valid on an UNKNOWN status")

    @property
    def ran(self) -> bool:
        """Report whether the pass reached a verdict for this tool.

        Returns:
            bool: False only when the residual is unknown.
        """
        return self.status is not VerifyStatus.UNKNOWN


@dataclass(frozen=True)
class VerifyScope:
    """The file set the verify pass will run over, and how it was chosen.

    Attributes:
        files: Absolute paths to verify.
        narrowed: True when fingerprints selected the set; False when the
            pass fell back to the documented floor.
        floor_reason: Why the floor was used, empty when ``narrowed``.
        targets: What to hand the verifying tools instead of ``files``. Set
            only on the floor of an unnarrowed run, where the run's original
            scan paths cover the same set far more cheaply than thousands of
            file arguments. Empty under ``--incremental`` or ``--diff``, where
            the scan paths cover strictly more than the candidates do.
    """

    files: tuple[str, ...]
    narrowed: bool
    floor_reason: str = NARROWED_REASON
    targets: tuple[str, ...] = ()

    @property
    def scan_targets(self) -> tuple[str, ...]:
        """Return what to hand the verifying tools as their scan targets.

        A narrowed scope hands over the changed files themselves. The floor
        of an unnarrowed run hands over the run's original paths instead of
        thousands of individual file arguments: the set is the same, and
        letting each tool do its own discovery keeps the fallback from being
        pathologically slower than the run it is verifying. Under
        ``--incremental`` or ``--diff`` that equivalence does not hold, so the
        floor names the candidate files.

        Returns:
            Scan targets for ``tool.check``.
        """
        return self.targets or self.files

    @property
    def summary(self) -> str:
        """Describe the scope in one console-ready clause.

        Returns:
            A string such as ``"12 changed file(s)"`` or ``"340 file(s)
            (coarse mtime resolution)"``.
        """
        if self.narrowed:
            return f"{len(self.files)} changed file(s)"
        return f"{len(self.files)} file(s) ({self.floor_reason})"


@dataclass(frozen=True)
class VerifyBaseline:
    """Fingerprints taken before the mutation phase, plus the floor set.

    Attributes:
        candidates: Every file a mutating capability could be handed. This is
            the documented floor the pass degrades to.
        snapshot: Fingerprints of those files as of just before mutation.
        scan_paths: The run's original scan targets, handed to the verifying
            tools when the pass falls back to the floor. Empty when the run
            was narrowed by ``--incremental`` or ``--diff``, because the scan
            paths then cover more than the candidates do.
    """

    candidates: tuple[str, ...]
    snapshot: FingerprintSnapshot = field(
        default_factory=lambda: FingerprintSnapshot(fingerprints={}),
    )
    scan_paths: tuple[str, ...] = ()


def _incremental_subset(*, tool_name: str, files: Sequence[str]) -> list[str]:
    """Restrict a tool's file set to what its incremental cache calls changed.

    Deliberately not ``walk_files_with_excludes(incremental=True)``: that call
    *writes* the tool's cache as a side effect, and this runs before the
    mutation phase, so it would tell every tool its own files were already
    up to date. Reading the cache is enough.

    Args:
        tool_name: Registry key whose cache to consult.
        files: Files the tool would otherwise be handed.

    Returns:
        The subset the tool's cache reports as changed since its last run.
    """
    from lintro.utils.file_cache import ToolCache

    return ToolCache.load(tool_name).get_changed_files(list(files))


def capture_verify_baseline(
    *,
    tools_to_run: Sequence[str],
    paths: Sequence[str],
    exclude: str | None,
    include_venv: bool,
    incremental: bool = False,
    diff_base: str | None = None,
) -> VerifyBaseline:
    """Fingerprint every file the mutation phase could rewrite.

    The candidate set is built per tool and unioned, not from one walk over
    the union of every mutating pattern. That difference only shows up under
    ``--incremental``, where each tool has its own idea of what changed, but
    getting it wrong would let the floor re-check files this run could never
    have touched and report their diagnostics as its residual.

    Args:
        tools_to_run: Tools selected for the run.
        paths: Scan targets given to the run.
        exclude: Comma-separated CLI exclude patterns, or ``None``.
        include_venv: Whether virtual-environment directories are in scope.
        incremental: Whether the run only scans files changed since the tool's
            last run. Applied per tool, from its own cache.
        diff_base: Resolved ``--diff`` base ref, or ``None``. Restricts the
            candidates the same way the mutation phase was restricted.

    Returns:
        VerifyBaseline: The floor file set and its pre-mutation fingerprints.
        Empty when no selected tool declares a pattern-addressed mutating
        claim. An empty baseline is not a no-op: ``run_verify_pass`` still
        emits an outcome per verifying tool, and the fold then carries every
        pre-fix finding and fails the run, because nothing was verified.
    """
    patterns_by_tool = _mutating_patterns_by_tool(tools_to_run)
    if not patterns_by_tool:
        return VerifyBaseline(candidates=())

    # Resolve excludes the way a plugin's own discovery does — CLI patterns
    # plus the built-in defaults and ``.lintro-ignore`` — so the floor is the
    # files the mutating tools would actually be handed, not every file on
    # disk that happens to match a claimed pattern.
    exclude_patterns = setup_exclude_patterns(
        [p.strip() for p in (exclude or "").split(",") if p.strip()],
    )
    candidates: set[str] = set()
    for tool_name, patterns in patterns_by_tool.items():
        files = walk_files_with_excludes(
            paths=list(paths),
            file_patterns=patterns,
            exclude_patterns=exclude_patterns,
            include_venv=include_venv,
            diff_base=diff_base,
        )
        if incremental:
            files = _incremental_subset(tool_name=tool_name, files=files)
        candidates.update(files)

    ordered = tuple(sorted(candidates))
    return VerifyBaseline(
        candidates=ordered,
        snapshot=snapshot_fingerprints(ordered),
        # Handing the tools the original scan paths is a cheap shortcut that
        # only holds when the candidate set *is* everything under them. Once
        # the run is narrowed by --incremental or --diff it is not, so the
        # floor must name the files instead of re-widening to the tree.
        scan_paths=() if (incremental or diff_base) else tuple(paths),
    )


def resolve_verify_scope(baseline: VerifyBaseline) -> VerifyScope:
    """Re-stat the baseline and decide which files the verify pass covers.

    Args:
        baseline: Fingerprints captured before the mutation phase.

    Returns:
        VerifyScope: The narrowed set when fingerprints are trustworthy,
        otherwise the full candidate set with the reason recorded.
    """
    if not baseline.candidates:
        return VerifyScope(files=(), narrowed=True)
    # Unreadable first: a snapshot with a failed stat is also not reliable, so
    # the order is what decides which of the two reasons the run reports.
    if baseline.snapshot.unreadable or len(baseline.snapshot.fingerprints) != len(
        baseline.candidates,
    ):
        return VerifyScope(
            files=baseline.candidates,
            narrowed=False,
            floor_reason=UNREADABLE_REASON,
            targets=baseline.scan_paths,
        )
    if not baseline.snapshot.is_reliable:
        return VerifyScope(
            files=baseline.candidates,
            narrowed=False,
            floor_reason=COARSE_MTIME_REASON,
            targets=baseline.scan_paths,
        )
    return VerifyScope(
        files=tuple(baseline.snapshot.changed_paths()),
        narrowed=True,
    )


def _issue_path(issue: BaseIssue, *, cwd: str | None) -> str:
    """Resolve an issue's file to an absolute path for scope comparison.

    Args:
        issue: The issue to locate.
        cwd: Working directory the tool ran in, used to anchor relative paths.

    Returns:
        The absolute path, or an empty string when the issue names no file.
    """
    raw = getattr(issue, "file", "") or ""
    if not raw:
        return ""
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(cwd or os.getcwd(), raw))


def _pre_fix_issues(result: ToolResult) -> list[BaseIssue]:
    """Return the issues a tool saw before it started rewriting files.

    Args:
        result: A mutation-phase result.

    Returns:
        ``initial_issues`` when the tool recorded them, otherwise the issues
        it reported. A tool that records neither contributes nothing, which is
        correct: it had nothing to say about the files it left alone.
    """
    if result.initial_issues:
        return list(result.initial_issues)
    return list(result.issues) if result.issues else []


def run_verify_pass(
    *,
    tools_to_run: Sequence[str],
    scope: VerifyScope,
    configure: Callable[..., VerifiableTool],
) -> list[VerifyOutcome]:
    """Run the ``CHECK`` capability of every verifying tool over the scope.

    Every verifying tool gets an outcome, including one whose ``CHECK`` never
    ran. That matters because a mutating tool now reports ``remaining=0`` after
    a clean fix: treating "no verify row" as "trust that zero" would let a run
    with unfixable issues exit 0. An outcome that did not run carries no
    verified files, so the fold falls back to the tool's pre-fix findings for
    *every* file — the same answer the mutation phase would have given before
    this pipeline existed.

    Args:
        tools_to_run: Tools selected for the run, in execution order.
        scope: The file set to verify.
        configure: Callable taking ``tool_name`` and returning the configured,
            check-mode plugin copy to execute. Supplied by the executor so
            this module stays free of configuration concerns.

    Returns:
        list[VerifyOutcome]: One outcome per verifying tool.

    Raises:
        TypeError: If a programming error occurs while a check runs.
        AttributeError: If a programming error occurs while a check runs.
    """
    outcomes: list[VerifyOutcome] = []
    files = list(scope.scan_targets)
    for name, resolvable in _verify_targets(tools_to_run):
        if not resolvable:
            # Nothing is known about this tool, so nothing about its residual
            # can be trusted either.
            outcomes.append(
                VerifyOutcome(
                    tool=name,
                    result=None,
                    status=VerifyStatus.UNKNOWN,
                    unknown_reason=UNRESOLVABLE_REASON,
                ),
            )
            continue
        if not scope.files:
            # Nothing was rewritten, so nothing needs re-checking. The tool's
            # pre-fix findings are still its post-fix findings.
            outcomes.append(
                VerifyOutcome(
                    tool=name,
                    result=None,
                    status=VerifyStatus.UNCHANGED,
                ),
            )
            continue
        started = time.monotonic()
        try:
            tool = configure(tool_name=name)
            result = tool.check(files, {})
        except (TypeError, AttributeError):
            # Programming errors propagate, exactly as they do out of the
            # mutation phase. Swallowing them here would make a bug in the
            # verify path indistinguishable from a tool that genuinely could
            # not run.
            raise
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            # A brand-new failure path: the row that follows flips to failed
            # with a note that reads like a lint finding, so say plainly that
            # the residual is unknown rather than measured. The traceback
            # stays at debug so the default level keeps one line per tool.
            logger.warning(
                f"Verify pass for {name} could not run: "
                f"{type(exc).__name__}: {exc}",
            )
            logger.opt(exception=True).debug(f"Verify pass failed for {name}")
            outcomes.append(
                VerifyOutcome(
                    tool=name,
                    result=None,
                    status=VerifyStatus.UNKNOWN,
                    unknown_reason=CRASHED_REASON.format(
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                ),
            )
            continue
        if result.timed_out:
            # A timed-out CHECK examined only part of its target set — a
            # multi-root aggregator such as golangci-lint still returns the
            # findings the roots that finished produced — so it is no verdict
            # over the scope at all.
            outcomes.append(
                VerifyOutcome(
                    tool=name,
                    result=None,
                    status=VerifyStatus.UNKNOWN,
                    unknown_reason=TIMED_OUT_REASON,
                ),
            )
            continue
        if result.skipped:
            # The check returned without executing — a version gate, most
            # often. ``success=True, issues_count=0`` is the shape of a clean
            # verdict, but no file was examined, so trusting it would drop the
            # tool's pre-fix findings as "fixed". Report it as unverified.
            outcomes.append(
                VerifyOutcome(
                    tool=name,
                    result=None,
                    status=VerifyStatus.UNKNOWN,
                    unknown_reason=SKIPPED_REASON,
                ),
            )
            continue
        if result.no_files:
            # The tool's own discovery matched none of the scope's files, so
            # it verified nothing. Unlike a skip this is routine — the scope is
            # the union over every mutating tool, so a tool that rewrote
            # nothing is handed another tool's files — and it means this tool
            # rewrote nothing either. Its pre-fix findings simply stand.
            outcomes.append(
                VerifyOutcome(
                    tool=name,
                    result=None,
                    status=VerifyStatus.UNCHANGED,
                ),
            )
            continue
        result.capability = Cap.CHECK
        result.duration_seconds = time.monotonic() - started
        outcomes.append(
            VerifyOutcome(
                tool=name,
                result=result,
                status=VerifyStatus.VERIFIED,
            ),
        )
    return outcomes


def _verified_paths(
    *,
    scope: VerifyScope,
    verify: ToolResult | None,
    fallback_cwd: str | None,
) -> set[str]:
    """Return every file the verify pass can be said to have covered.

    The narrowed scope is the *intent*; it is not the whole answer. Several
    fix-capable tools ignore the paths they are handed and check their whole
    project — clippy runs ``cargo clippy`` from the crate root with no file
    arguments, golangci-lint does the same from the module root — so their
    ``CHECK`` reports on files the scope never named. Counting only
    ``scope.files`` as verified would carry those files' pre-fix findings as
    survivors *and* append the same findings again from the verify result,
    inflating ``remaining``, deflating ``fixed``, and leaving a residual that
    can never reach zero.

    A file the check actually reported on has been re-examined by definition,
    whatever the scope asked for, so its verdict replaces the pre-fix one.

    Args:
        scope: The file set the verify pass was asked to cover.
        verify: The tool's ``CHECK`` result, or ``None`` when it produced no
            verdict — in which case nothing was verified at all.
        fallback_cwd: Directory to resolve the verify result's relative paths
            against when it records none of its own. This is the mutation
            result's ``cwd``: a tool's check and its fix run from the same
            place (clippy and rustfmt from the crate root, everything else
            from ``prepare``'s working directory), and only the fix side is
            stamped today. Without it a crate-relative ``src/lib.rs`` would be
            keyed under the *process* directory and match nothing, so the
            union this function exists for would never engage.

    Returns:
        Absolute paths whose pre-fix findings are superseded.
    """
    if verify is None:
        return set()
    covered = set(scope.files)
    for issue in verify.issues or ():
        path = _issue_path(issue, cwd=verify.cwd or fallback_cwd)
        if path:
            covered.add(path)
    return covered


def _fold_unknown(
    *,
    mutation: ToolResult,
    outcome: VerifyOutcome,
) -> ToolResult:
    """Mark a tool's residual as unmeasured rather than inventing a number.

    "The check could not tell us" is a third state beside "clean" and "N
    remaining", and it has to survive all the way to the display. Carrying the
    pre-fix findings and calling the difference ``fixed`` would report a
    measurement this run never took: the only honest after-count is none at
    all. The run fails, so an unknown residual can never be read as a pass.

    Args:
        mutation: The tool's mutation-phase result.
        outcome: The tool's ``UNKNOWN`` verify outcome.

    Returns:
        ToolResult: ``mutation`` carrying its pre-fix findings, no fixed or
        remaining count, and the reason the residual is unknown.
    """
    detected = _pre_fix_issues(mutation)
    note = RESIDUAL_UNKNOWN_TEMPLATE.format(
        reason=outcome.unknown_reason,
        detected=len(detected),
    )
    output = mutation.output or ""
    mutation.output = f"{output}\n{note}" if output.strip() else note
    mutation.issues = detected
    mutation.issues_count = len(detected)
    if mutation.initial_issues_count is None:
        mutation.initial_issues_count = len(detected)
    # No after-count exists, so none is reported. Consumers key off
    # ``residual_unknown`` and render "unknown" rather than filling the gap
    # with a zero or with the pre-fix number.
    mutation.fixed_issues_count = None
    mutation.remaining_issues_count = None
    mutation.residual_unknown = True
    mutation.residual_unknown_reason = outcome.unknown_reason
    mutation.success = False
    return mutation


def _fold_one(
    *,
    mutation: ToolResult,
    outcome: VerifyOutcome,
    scope: VerifyScope,
) -> ToolResult:
    """Replace a mutation result's residual with the authoritative one.

    Args:
        mutation: The tool's mutation-phase result.
        outcome: The tool's verify-pass outcome.
        scope: The file set the verify pass covered.

    Returns:
        ToolResult: ``mutation`` with the verify pass's residual, the derived
        fixed count, and a note when the two disagreed. The pre-fix issue list
        is preserved so the "detected / remaining" view still renders.
    """
    if outcome.status is VerifyStatus.UNKNOWN:
        return _fold_unknown(mutation=mutation, outcome=outcome)
    verify = outcome.result
    # An ``UNCHANGED`` outcome carries no result: nothing was rewritten, or
    # the tool discovered none of the scope's files, so its pre-fix findings
    # stand as its post-fix findings. Everything that could not answer at all
    # left through ``_fold_unknown`` above. The guard below repeats the shape
    # checks anyway so a hand-built ``VERIFIED`` outcome cannot smuggle a
    # skipped, timed-out or no-files result in as a verdict: a timeout in
    # particular looks like an answer — a multi-root aggregator returns the
    # findings the roots that finished produced — and is not one over the
    # scope.
    check_answered = (
        verify is not None
        and not verify.skipped
        and not verify.no_files
        and not verify.timed_out
        and (verify.success or bool(verify.issues))
    )
    verified_paths = _verified_paths(
        scope=scope,
        verify=verify if check_answered else None,
        fallback_cwd=mutation.cwd,
    )
    survivors: list[BaseIssue] = [
        issue
        for issue in _pre_fix_issues(mutation)
        if _issue_path(issue, cwd=mutation.cwd) not in verified_paths
    ]
    if check_answered and verify is not None and verify.issues:
        # Only an answered CHECK contributes findings. A partial one — a
        # multi-root aggregator that timed out on one root — carries real
        # findings from the roots that finished, but every pre-fix finding is
        # already being carried above, so appending them would double-count.
        survivors.extend(list(verify.issues))

    residual = len(survivors)
    initial = mutation.initial_issues_count
    if initial is None:
        initial = len(_pre_fix_issues(mutation))
    fixed = max(0, initial - residual)

    previous = mutation.remaining_issues_count
    if previous is None:
        previous = mutation.issues_count
    output = mutation.output or ""
    if previous != residual:
        note = VERIFY_NOTE_TEMPLATE.format(residual=residual, previous=previous)
        output = f"{output}\n{note}" if output.strip() else note

    mutation.issues = survivors
    mutation.issues_count = residual
    mutation.initial_issues_count = fixed + residual
    mutation.fixed_issues_count = fixed
    mutation.remaining_issues_count = residual
    mutation.output = output
    # ``success`` follows the authoritative residual, not the fix pass's
    # opinion of itself: a leftover on a file the pass did not need to verify
    # is still a leftover. The mutation phase's own flag is still ANDed in so
    # an execution failure that produced no issues stays a failure, and so is
    # the verify's, so a broken check cannot read as clean.
    mutation.success = mutation.success and residual == 0
    if verify is not None:
        mutation.success = mutation.success and verify.success
        if verify.duration_seconds is not None:
            mutation.duration_seconds = (
                mutation.duration_seconds or 0.0
            ) + verify.duration_seconds
    return mutation


def fold_verify_results(
    *,
    mutation_results: list[ToolResult],
    verify_results: Sequence[VerifyOutcome],
    scope: VerifyScope,
) -> None:
    """Fold each verify outcome into its tool's mutation result, in place.

    Display rolls up to the tool, so the run keeps exactly one result per
    tool: the mutation result carries what was fixed and, after this fold, the
    verify pass's residual. Keeping both rows instead would have made every
    output formatter, SARIF writer and AI summary responsible for a grouping
    rule for no user-visible gain.

    A tool with no outcome at all declares no ``CHECK`` capability (prettier is
    ``FORMAT``-only) and keeps its own numbers.

    Args:
        mutation_results: Results from the mutation phase, mutated in place.
        verify_results: Outcomes from the verify pass.
        scope: The file set the verify pass covered.
    """
    by_name = {outcome.tool: outcome for outcome in verify_results}
    for index, mutation in enumerate(mutation_results):
        if mutation.skipped or mutation.timed_out:
            continue
        outcome = by_name.get(mutation.name)
        if outcome is None:
            continue
        mutation_results[index] = _fold_one(
            mutation=mutation,
            outcome=outcome,
            scope=scope,
        )
