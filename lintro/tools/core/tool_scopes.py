"""Run-scoped write sets: what each tool may rewrite on this run (#2606).

The derived scheduler (:mod:`lintro.tools.core.scheduler`) needs to know when
two mutating tools can reach the same bytes. Comparing glob strings cannot
answer that — ``Cargo.toml`` and ``*.toml`` never compare equal, ``*.py`` and
``test_*.py`` never relate — so the comparison is made on the **files
themselves**: each mutating tool's run-scoped candidate list is resolved the
same way :func:`lintro.tools.core.verify_pass.capture_verify_baseline`
resolves it for fingerprinting, canonicalised with ``os.path.realpath``, and
two tools overlap when those sets intersect.

Three cases cannot be answered by a file list, and all three resolve the
conservative way — when in doubt the batch is split, because a false positive
costs one batch while a false negative reintroduces a lost write:

- **Project-scoped writers.** A mutating tool that declares no patterns, or
  one whose definition sets ``partitionable=False`` (clippy, golangci-lint),
  expands from the handed paths to a whole project root and rewrites files no
  pattern named — ``Cargo.lock`` is not in clippy's claim. Its scope is the
  run's scan roots, so it conflicts with every writer whose candidates fall
  under one of them.
- **Unknown tools.** A name the registry cannot resolve is a tool nothing is
  known about. It is treated as a project-scoped writer and therefore lands in
  a batch of its own. A *registered* tool that declares no claims is a
  different thing — commitlint deliberately claims nothing — and keeps the
  unordered, unconstrained position it has always had.
- **No paths.** A caller that orders tools without naming a scan scope
  (``lintro config``, ``lintro list-tools``) gets no candidate lists at all.
  :mod:`lintro.tools.core.scheduler` then falls back to asking whether two
  patterns *could* match one file, which over-approximates in the same safe
  direction.

This module is the file-system half and is deliberately separate from the
graph half: resolving a scope walks the tree, and the scheduler must stay
callable without one.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.enums.capability import MUTATING_CAPABILITIES, Cap

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.claim import Claim

__all__ = [
    "MAX_SCOPE_LABELS",
    "PROJECT_SCOPE_LABEL",
    "ToolScope",
    "overlap_label",
    "patterns_may_overlap",
    "resolve_tool_scopes",
    "scopes_may_overlap_by_pattern",
    "scopes_overlap",
    "write_conflict",
]

#: Label used when an overlap cannot be attributed to a file extension.
PROJECT_SCOPE_LABEL: str = "(project scope)"

#: Cap on the number of pattern labels rendered for one overlap.
MAX_SCOPE_LABELS: int = 3


@dataclass(frozen=True)
class ToolScope:
    """What one tool may rewrite on this run.

    Attributes:
        tool: Registry name, lowercased.
        candidates: Canonical (``realpath``) paths the tool's mutating claims
            could be handed. Empty for a project-scoped or unknown writer,
            whose reach is described by ``roots`` instead.
        roots: Canonical scan roots a project-scoped writer expands to. Empty
            when the roots are unknown, which means "everywhere".
        patterns: Sorted glob patterns the tool's mutating claims declare.
            Used only by the no-paths fallback.
        mutating_capabilities: The tool's declared ``FIX``/``FORMAT`` set.
        declares_format: Whether the tool declares ``FORMAT`` anywhere. Drives
            format-owner demotion.
        project_scoped: Whether the tool expands to a project root.
        known: Whether the scheduler could read the tool's claims at all.
        resolved: Whether ``candidates`` was actually resolved against the
            filesystem. False when the caller named no scan paths.
    """

    tool: str
    candidates: frozenset[str] = frozenset()
    roots: frozenset[str] = frozenset()
    patterns: tuple[str, ...] = ()
    mutating_capabilities: frozenset[Cap] = frozenset()
    declares_format: bool = False
    project_scoped: bool = False
    known: bool = True
    resolved: bool = False

    @property
    def is_writer(self) -> bool:
        """Report whether this tool can rewrite a file on this run.

        Returns:
            True for a tool with a mutating capability, and for an unknown
            tool, which is assumed to write until it says otherwise.
        """
        return bool(self.mutating_capabilities) or not self.known

    @property
    def unbounded(self) -> bool:
        """Report whether this tool's reach cannot be bounded by a file list.

        Returns:
            True for a project-scoped or unknown writer.
        """
        return self.project_scoped or not self.known


def _definition_for(tool_name: str) -> object | None:
    """Resolve a tool definition out of the registry, tolerating a miss.

    Ordering must never be the thing that fails a run, so an unresolvable
    name returns ``None`` and is scheduled conservatively rather than raising.

    Args:
        tool_name: Registry name (case-insensitive).

    Returns:
        The tool definition, or ``None`` when the name cannot be resolved.
    """
    from lintro.tools import tool_manager

    try:
        return tool_manager.get_tool(tool_name).definition
    except (KeyError, ValueError, RuntimeError, AttributeError):
        return None


def _mutating_facts(
    claims: Sequence[Claim],
) -> tuple[set[str], set[Cap], bool, bool]:
    """Summarise the mutating half of a tool's claims.

    Args:
        claims: The tool's declared claims.

    Returns:
        ``(patterns, mutating capabilities, declares FORMAT, patternless)``.
        ``patternless`` is True when a mutating claim declares no patterns,
        which is what makes a claim project-scoped.
    """
    patterns: set[str] = set()
    capabilities: set[Cap] = set()
    patternless = False
    declares_format = False
    for claim in claims:
        if Cap.FORMAT in claim.capabilities:
            declares_format = True
        if not claim.is_mutating:
            continue
        capabilities |= set(claim.capabilities & MUTATING_CAPABILITIES)
        if claim.patterns:
            patterns.update(claim.patterns)
        else:
            patternless = True
    return patterns, capabilities, declares_format, patternless


def _canonical(paths: Sequence[str]) -> frozenset[str]:
    """Canonicalise paths so two spellings of one file compare equal.

    Args:
        paths: Filesystem paths.

    Returns:
        The same paths as ``realpath`` results.
    """
    return frozenset(os.path.realpath(path) for path in paths)


def resolve_tool_scopes(
    tool_names: Sequence[str],
    *,
    paths: Sequence[str] | None = None,
    exclude: str | None = None,
    include_venv: bool = False,
    diff_base: str | None = None,
) -> dict[str, ToolScope]:
    """Resolve what each named tool may rewrite on this run.

    Args:
        tool_names: Tools selected for the run (lowercased by the caller).
        paths: Scan targets. When ``None`` or empty, no candidate list is
            resolved and the scopes carry ``resolved=False``; the scheduler
            then falls back to conservative pattern comparison.
        exclude: Comma-separated CLI exclude patterns, or ``None``.
        include_venv: Whether virtual-environment directories are in scope.
        diff_base: Resolved ``--diff`` base ref, or ``None``.

    Returns:
        One :class:`ToolScope` per name, keyed by the lowercased name.
    """
    from lintro.utils.path_filtering import (
        setup_exclude_patterns,
        walk_files_with_excludes,
    )

    scan_paths = list(paths or ())
    resolve = bool(scan_paths)
    exclude_patterns = (
        setup_exclude_patterns(
            [part.strip() for part in (exclude or "").split(",") if part.strip()],
        )
        if resolve
        else []
    )
    roots = _canonical(scan_paths) if resolve else frozenset()

    scopes: dict[str, ToolScope] = {}
    for raw_name in tool_names:
        name = raw_name.lower()
        definition = _definition_for(name)
        if definition is None:
            # Nothing is known about this tool's reach. Treat it as a
            # project-scoped writer so it never shares a batch with another
            # writer.
            scopes[name] = ToolScope(
                tool=name,
                roots=roots,
                project_scoped=True,
                known=False,
                resolved=resolve,
            )
            continue

        claims = list(getattr(definition, "claims", None) or ())
        patterns, capabilities, declares_format, patternless = _mutating_facts(claims)
        project_scoped = bool(capabilities) and (
            patternless or getattr(definition, "partitionable", False) is False
        )
        candidates: frozenset[str] = frozenset()
        if resolve and capabilities and patterns and not project_scoped:
            candidates = _canonical(
                walk_files_with_excludes(
                    paths=scan_paths,
                    file_patterns=sorted(patterns),
                    exclude_patterns=exclude_patterns,
                    include_venv=include_venv,
                    diff_base=diff_base,
                ),
            )
        scopes[name] = ToolScope(
            tool=name,
            candidates=candidates,
            roots=roots if project_scoped else frozenset(),
            patterns=tuple(sorted(patterns)),
            mutating_capabilities=frozenset(capabilities),
            declares_format=declares_format,
            project_scoped=project_scoped,
            known=True,
            resolved=resolve,
        )
    return scopes


def _under_any_root(path: str, roots: frozenset[str]) -> bool:
    """Report whether a canonical path sits under one of the given roots.

    Args:
        path: Canonical path to test.
        roots: Canonical root paths. An empty set means "unbounded", which
            answers True for every path.

    Returns:
        True when the path is the root itself or lives beneath it.
    """
    if not roots:
        return True
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def scopes_overlap(left: ToolScope, right: ToolScope) -> bool:
    """Report whether two tools can rewrite the same file on this run.

    Only writers conflict: a ``CHECK`` capability reads, so it never produces
    a conflict edge and check-mode batching is untouched.

    Args:
        left: One tool's scope.
        right: The other tool's scope.

    Returns:
        True when the two scopes may reach the same bytes.
    """
    if not (left.is_writer and right.is_writer):
        return False
    if left.unbounded and right.unbounded:
        return True
    if left.unbounded:
        return not right.candidates or any(
            _under_any_root(path, left.roots) for path in right.candidates
        )
    if right.unbounded:
        return not left.candidates or any(
            _under_any_root(path, right.roots) for path in left.candidates
        )
    return bool(left.candidates & right.candidates)


def _label_for(path: str) -> str:
    """Describe one overlapping file as the pattern class it belongs to.

    Args:
        path: Canonical path.

    Returns:
        ``*.py`` for an extensioned file, the basename otherwise.
    """
    base = os.path.basename(path)
    stem, dot, ext = base.rpartition(".")
    if dot and stem and ext:
        return f"*.{ext}"
    return base


def overlap_label(left: ToolScope, right: ToolScope) -> str:
    """Describe the scope two tools overlap on, for explain output.

    Args:
        left: One tool's scope.
        right: The other tool's scope.

    Returns:
        A short pattern-shaped label such as ``"*.py"``, or
        :data:`PROJECT_SCOPE_LABEL` when the overlap is a whole project root.
    """
    shared = left.candidates & right.candidates
    if shared:
        labels = sorted({_label_for(path) for path in shared})
        head = labels[:MAX_SCOPE_LABELS]
        rendered = ", ".join(head)
        return rendered if len(labels) == len(head) else f"{rendered}, ..."
    if left.unbounded or right.unbounded:
        return PROJECT_SCOPE_LABEL
    labels = sorted(set(left.patterns) & set(right.patterns)) or sorted(
        set(left.patterns) | set(right.patterns),
    )
    head = labels[:MAX_SCOPE_LABELS]
    return ", ".join(head) if head else PROJECT_SCOPE_LABEL


def patterns_may_overlap(left: str, right: str) -> bool:
    """Report whether two glob patterns could ever match one file.

    The no-paths fallback. It answers "provably disjoint?" rather than
    "equal?", so ``Cargo.toml`` relates to ``*.toml`` and ``*.py`` relates to
    ``test_*.py``, and it errs towards True.

    Args:
        left: One glob pattern.
        right: The other glob pattern.

    Returns:
        False only when the two patterns provably cannot match one name.
    """
    if left == right or "*" in (left, right):
        return True
    left_wild = any(ch in left for ch in "*?[")
    right_wild = any(ch in right for ch in "*?[")
    if not left_wild and not right_wild:
        return left == right
    if not left_wild:
        return fnmatch.fnmatch(left, right)
    if not right_wild:
        return fnmatch.fnmatch(right, left)
    return _wild_extension(left) == _wild_extension(right)


def _wild_extension(pattern: str) -> str | None:
    """Return a wildcard pattern's literal extension, when it has one.

    Args:
        pattern: A glob pattern containing at least one wildcard.

    Returns:
        The extension after the final dot when it holds no wildcard, else
        ``None`` — which compares unequal to nothing and therefore keeps the
        answer conservative only when both sides are ``None``.
    """
    _, dot, ext = pattern.rpartition(".")
    if not dot or not ext or any(ch in ext for ch in "*?["):
        return None
    return ext


def scopes_may_overlap_by_pattern(left: ToolScope, right: ToolScope) -> bool:
    """Pattern-level overlap test used when no scan paths were named.

    Args:
        left: One tool's scope.
        right: The other tool's scope.

    Returns:
        True when the two writers declare patterns that could match one file.
    """
    if not (left.is_writer and right.is_writer):
        return False
    if left.unbounded or right.unbounded:
        return True
    return any(
        patterns_may_overlap(one, other)
        for one in left.patterns
        for other in right.patterns
    )


def write_conflict(left: ToolScope, right: ToolScope) -> bool:
    """Report a write conflict using whichever evidence this run has.

    Args:
        left: One tool's scope.
        right: The other tool's scope.

    Returns:
        True when the two tools must not share a batch.
    """
    if left.resolved and right.resolved:
        return scopes_overlap(left, right)
    return scopes_may_overlap_by_pattern(left, right)
