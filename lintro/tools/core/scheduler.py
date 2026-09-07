"""Derivation of execution order from declared tool claims.

Step 4 of epic #1735 (#1742). This module is the **authoritative** scheduler:
:meth:`lintro.tools.core.tool_manager.ToolManager.get_tool_execution_order`
returns what :func:`derive_execution_order` computes here, and the scalar
``priority`` system it replaced (``DEFAULT_TOOL_PRIORITIES``,
``ToolDefinition.priority``, ``execution.tool_order``) is deleted. The same
derivation backs ``lintro check --explain-order``, ``lintro fmt
--explain-order``, the ``lintro doctor`` order section and ``lintro config``,
so every report shows the order that actually runs.

Derivation rules:

- **Phase per pattern.** For one glob pattern, a tool occupies the earliest
  phase it holds there, in the order ``FIX`` -> ``FORMAT`` -> ``CHECK``. A
  tool is invoked once, so a tool that both fixes and checks ``*.py`` sits in
  the ``FIX`` phase and its diagnostics come out of the same invocation. This
  is what keeps ruff ``{FIX, FORMAT, CHECK}`` from contesting black
  ``{FORMAT, CHECK}`` in both directions on ``*.py``, and it is what makes
  ``ruff -> black`` fall out of the model instead of needing the deleted
  ``[tool.lintro.post_checks]`` workaround.
- **Edges.** Within a pattern, every earlier-phase tool precedes every
  later-phase tool. Equal phases produce no edge: that is a proven
  independence, so the tie breaks alphabetically.
- **Pattern universe.** Patterns are compared literally, plus the single
  subsumption a universal claim gives: a tool claiming ``*`` (typos,
  gitleaks, trufflehog) joins every pattern group. No glob-to-glob semantics
  beyond that are attempted, so ``*.py`` and ``test_*.py`` stay separate
  groups.
- **Project-scoped claims.** A claim with no patterns (osv-scanner) is not
  addressed by pattern and therefore produces no edges.
- **Cycles.** Detected before linearisation and reported with the tools and
  the patterns whose edges close them. Linearisation stays deterministic and
  total: a stalled topological sort emits the alphabetically first remaining
  tool, so a cycle degrades ordering rather than failing a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.enums.capability import Cap

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lintro.models.core.claim import Claim

#: Phases in the order they must run for one pattern.
PHASE_ORDER: tuple[Cap, ...] = (Cap.FIX, Cap.FORMAT, Cap.CHECK)

_PHASE_RANK: dict[Cap, int] = {cap: rank for rank, cap in enumerate(PHASE_ORDER)}


@dataclass(frozen=True)
class OrderEdge:
    """A single derived "runs before" constraint between two tools.

    Attributes:
        before: Tool that must run first.
        after: Tool that must run second.
        pattern: Glob pattern whose claims produced the constraint.
        before_capability: Phase ``before`` occupies on ``pattern``.
        after_capability: Phase ``after`` occupies on ``pattern``.
    """

    before: str
    after: str
    pattern: str
    before_capability: Cap
    after_capability: Cap

    @property
    def reason(self) -> str:
        """Describe the edge in one human-readable clause.

        Returns:
            A string such as ``"*.py: ruff(fix) -> black(format)"``.
        """
        return (
            f"{self.pattern}: {self.before}({self.before_capability}) -> "
            f"{self.after}({self.after_capability})"
        )


@dataclass(frozen=True)
class OrderCycle:
    """A set of tools whose derived constraints form a cycle.

    Attributes:
        tools: Tools in the cycle, alphabetically sorted.
        edges: The derived edges internal to the cycle, sorted.
    """

    tools: tuple[str, ...]
    edges: tuple[OrderEdge, ...]

    @property
    def patterns(self) -> tuple[str, ...]:
        """Return the patterns that contributed an edge to this cycle.

        Returns:
            Sorted, de-duplicated pattern strings.
        """
        return tuple(sorted({edge.pattern for edge in self.edges}))


@dataclass(frozen=True)
class DerivedOrder:
    """The order derived from claims, with the graph it came from.

    Attributes:
        tools: Tool names in derived execution order.
        edges: Every derived constraint, sorted.
        cycles: Cycles found before linearisation (empty when the graph is a
            DAG).
    """

    tools: tuple[str, ...]
    edges: tuple[OrderEdge, ...]
    cycles: tuple[OrderCycle, ...]


def _claim_covers(claim_patterns: Sequence[str], pattern: str) -> bool:
    """Report whether a claim's patterns cover a pattern group.

    Args:
        claim_patterns: Patterns declared by one claim.
        pattern: The pattern group being tested.

    Returns:
        True when a declared pattern equals ``pattern``, or when the claim is
        the universal ``*``. No other glob-to-glob subsumption is attempted:
        ``*.py`` does not join a ``test_*.py`` group.
    """
    return any(p in {"*", pattern} for p in claim_patterns)


def _phase_for(claims: Sequence[Claim], pattern: str) -> Cap | None:
    """Return the phase a tool occupies for one pattern.

    Args:
        claims: The tool's declared claims.
        pattern: Pattern group to resolve.

    Returns:
        The earliest capability the tool holds on ``pattern``, or None when
        the tool does not claim it.
    """
    best: Cap | None = None
    for claim in claims:
        if not claim.patterns or not _claim_covers(claim.patterns, pattern):
            continue
        for cap in claim.capabilities:
            if best is None or _PHASE_RANK[cap] < _PHASE_RANK[best]:
                best = cap
    return best


def _pattern_universe(claims_by_tool: Mapping[str, Sequence[Claim]]) -> list[str]:
    """Collect every pattern any tool declares.

    Args:
        claims_by_tool: Claims keyed by tool name.

    Returns:
        Sorted, de-duplicated pattern strings.
    """
    patterns: set[str] = set()
    for claims in claims_by_tool.values():
        for claim in claims:
            patterns.update(claim.patterns)
    return sorted(patterns)


def _edges_for_pattern(
    claims_by_tool: Mapping[str, Sequence[Claim]],
    pattern: str,
) -> list[OrderEdge]:
    """Derive the FIX -> FORMAT -> CHECK edges for one pattern.

    Args:
        claims_by_tool: Claims keyed by tool name.
        pattern: Pattern group to derive edges for.

    Returns:
        Edges sorted by ``(before, after)``.
    """
    phases: dict[str, Cap] = {}
    for name in sorted(claims_by_tool):
        phase = _phase_for(claims_by_tool[name], pattern)
        if phase is not None:
            phases[name] = phase

    edges: list[OrderEdge] = []
    for before in sorted(phases):
        for after in sorted(phases):
            if _PHASE_RANK[phases[before]] >= _PHASE_RANK[phases[after]]:
                continue
            edges.append(
                OrderEdge(
                    before=before,
                    after=after,
                    pattern=pattern,
                    before_capability=phases[before],
                    after_capability=phases[after],
                ),
            )
    return edges


def _adjacency(
    tools: Sequence[str],
    edges: Sequence[OrderEdge],
) -> dict[str, set[str]]:
    """Build a successor map from derived edges.

    Args:
        tools: Every tool in the graph.
        edges: Derived edges.

    Returns:
        Mapping of tool name to the tools that must run after it.
    """
    successors: dict[str, set[str]] = {name: set() for name in tools}
    for edge in edges:
        successors[edge.before].add(edge.after)
    return successors


def _reachability(successors: Mapping[str, set[str]]) -> dict[str, set[str]]:
    """Compute the transitive closure of a successor map.

    The graphs here are at most a few dozen nodes, so a fixpoint over the
    successor sets is simpler than Tarjan and just as deterministic.

    Args:
        successors: Direct successor map.

    Returns:
        Mapping of tool name to every tool reachable from it.
    """
    reach: dict[str, set[str]] = {n: set(s) for n, s in successors.items()}
    changed = True
    while changed:
        changed = False
        for node in sorted(reach):
            expanded = set(reach[node])
            for nxt in reach[node]:
                expanded |= reach[nxt]
            if expanded != reach[node]:
                reach[node] = expanded
                changed = True
    return reach


def _find_cycles(
    tools: Sequence[str],
    edges: Sequence[OrderEdge],
) -> tuple[OrderCycle, ...]:
    """Find every strongly connected component larger than one tool.

    Args:
        tools: Every tool in the graph.
        edges: Derived edges.

    Returns:
        Cycles sorted by their first tool, each naming its tools and the
        edges (and therefore patterns) that close it.
    """
    reach = _reachability(_adjacency(tools, edges))
    seen: set[str] = set()
    cycles: list[OrderCycle] = []
    for node in sorted(tools):
        if node in seen:
            continue
        members = tuple(
            sorted(
                {node} | {other for other in reach[node] if node in reach[other]},
            ),
        )
        if len(members) == 1:
            continue
        seen.update(members)
        member_set = set(members)
        internal = tuple(
            sorted(
                (e for e in edges if e.before in member_set and e.after in member_set),
                key=_edge_sort_key,
            ),
        )
        cycles.append(OrderCycle(tools=members, edges=internal))
    return tuple(cycles)


def _edge_sort_key(edge: OrderEdge) -> tuple[str, str, str]:
    """Sort key giving edges a stable, readable order.

    Args:
        edge: Edge to key.

    Returns:
        ``(before, after, pattern)``.
    """
    return (edge.before, edge.after, edge.pattern)


def _linearize(tools: Sequence[str], edges: Sequence[OrderEdge]) -> tuple[str, ...]:
    """Topologically sort the derived graph, alphabetically breaking ties.

    When a cycle stalls the sort, the alphabetically first remaining tool is
    emitted so the result stays total and deterministic. Cycles are reported
    separately by :func:`_find_cycles`; this function never raises.

    Args:
        tools: Every tool to order.
        edges: Derived edges.

    Returns:
        Tool names in derived execution order.
    """
    successors = _adjacency(tools, edges)
    indegree: dict[str, int] = dict.fromkeys(tools, 0)
    for node in successors:
        for nxt in successors[node]:
            indegree[nxt] += 1

    remaining = set(tools)
    ordered: list[str] = []
    while remaining:
        ready = sorted(n for n in remaining if indegree[n] == 0)
        chosen = ready[0] if ready else sorted(remaining)[0]
        ordered.append(chosen)
        remaining.discard(chosen)
        for nxt in successors[chosen]:
            if nxt in remaining:
                indegree[nxt] -= 1
    return tuple(ordered)


def derive_order(claims_by_tool: Mapping[str, Sequence[Claim]]) -> DerivedOrder:
    """Derive an execution order from declared claims.

    Args:
        claims_by_tool: Claims keyed by tool name. A tool with no claims
            (commitlint) is unordered and keeps its alphabetical position.

    Returns:
        The derived order together with the edges and cycles behind it.
    """
    tools = sorted(claims_by_tool)
    edges: list[OrderEdge] = []
    for pattern in _pattern_universe(claims_by_tool):
        edges.extend(_edges_for_pattern(claims_by_tool, pattern))
    edges.sort(key=_edge_sort_key)
    return DerivedOrder(
        tools=_linearize(tools, edges),
        edges=tuple(edges),
        cycles=_find_cycles(tools, edges),
    )


def collect_tool_claims(tool_names: Sequence[str]) -> dict[str, list[Claim]]:
    """Read the declared claims for the named tools out of the registry.

    A tool that cannot be resolved, or whose definition predates ``claims``,
    contributes no claims and is therefore unordered. Ordering must not be the
    thing that fails a run: an unresolvable tool reaches the executor and is
    reported there as a failed result. Rejecting an unknown name outright is
    the job of :meth:`ToolManager.get_tool_execution_order`, the entry point
    the CLI uses, which resolves every name before ordering starts;
    ``get_parallel_batches`` deliberately does not, so batching stays as
    tolerant as this function.

    Args:
        tool_names: Tool names to look up (case-insensitive).

    Returns:
        Claims keyed by the lowercased tool name.
    """
    from lintro.tools import tool_manager

    claims: dict[str, list[Claim]] = {}
    for name in tool_names:
        try:
            definition = tool_manager.get_tool(name).definition
        except (KeyError, ValueError, RuntimeError, AttributeError):
            claims[name.lower()] = []
            continue
        claims[name.lower()] = list(getattr(definition, "claims", None) or ())
    return claims


def build_order_report(tool_names: Sequence[str]) -> DerivedOrder:
    """Derive the execution order for a set of tools, with its reasoning.

    Args:
        tool_names: Tool names to order (case-insensitive).

    Returns:
        The derived order together with the edges and cycles behind it.
    """
    normalized = [name.lower() for name in tool_names]
    return derive_order(collect_tool_claims(normalized))


def derive_execution_order(tool_names: Sequence[str]) -> list[str]:
    """Return the authoritative execution order for a set of tools.

    This is the single ordering authority: every caller that needs to know
    what runs when — the executor, ``--explain-order``, ``lintro doctor`` and
    ``lintro config`` — resolves it through this function, so a report can
    never print an order other than the one that runs.

    Ordering itself is total and fail-open: a name that resolves to no
    registered tool contributes no claims and stays in the result,
    unconstrained, because a bad name must not be something *ordering* fails
    on. Rejecting it is a separate concern and belongs to the entry point the
    CLI goes through, :meth:`ToolManager.get_tool_execution_order`, which
    resolves every name before calling this. Both halves are pinned by
    ``test_manager_rejects_an_unknown_name_the_scheduler_tolerates``.

    Args:
        tool_names: Tool names to order (case-insensitive).

    Returns:
        The same tool names, lowercased, in derived execution order.
    """
    return list(build_order_report(tool_names).tools)
