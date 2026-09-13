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
  later-phase tool. Equal *read-only* phases produce no edge: that is a
  proven independence, so the tie breaks alphabetically.
- **Pattern universe.** Patterns are compared literally, plus the single
  subsumption a universal claim gives: a tool claiming ``*`` (typos,
  gitleaks, trufflehog) joins every pattern group. No glob-to-glob semantics
  beyond that are attempted for *phase* edges, so ``*.py`` and ``test_*.py``
  stay separate groups there.
- **Write conflicts (#2606).** Phase edges alone let two mutators share a
  batch and race on one file's bytes. A second class of edge closes that: two
  writers whose run-scoped candidate sets intersect
  (:mod:`lintro.tools.core.tool_scopes`) never share a batch. Overlap is
  computed from canonical (``realpath``) file identities, not from glob
  strings, so ``Cargo.toml`` relates to ``*.toml`` and ``*.py`` relates to
  ``test_*.py``. Read-only capabilities never produce a conflict edge, so
  ``lintro check`` and the verify pass batch exactly as they did.
- **Precedence.** For a conflicting pair the *winner* is the authoritative
  writer: it runs last, so its write is the one that survives, and it keeps
  ``FORMAT``. The winner is chosen by, in order: a user override
  (``execution.precedence``), then ``FIX`` before ``FORMAT``, then the tool
  with fewer mutating capabilities (the authority rule — a dedicated
  formatter outranks a multi-capability tool), then the alphabetically last
  tool id.
- **Format-owner demotion (#1744).** When several conflicting tools declare
  ``FORMAT`` on one scope, only the winner formats it; every other tool's
  ``FORMAT`` is demoted for that scope while its ``CHECK`` stays enabled. The
  demotion is recorded on :class:`DerivedOrder` so explain, doctor and init
  can report it.
- **Project-scoped claims.** A claim with no patterns (osv-scanner) is not
  addressed by pattern and therefore produces no *phase* edges. A
  project-scoped *writer* still conflicts with every writer under its roots.
- **Cycles.** Detected before linearisation and reported with the tools and
  the patterns whose edges close them. A derived cycle degrades ordering
  rather than failing a run: a stalled topological sort emits the
  alphabetically first remaining tool, so linearisation stays deterministic
  and total. A cycle that a **configured** precedence override closed is
  different — it is a user error with a named fix, so planning raises
  :class:`OrderPlanningError` naming both tools and the config key rather
  than silently falling back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from lintro.enums.capability import Cap
from lintro.tools.core.tool_scopes import (
    ToolScope,
    overlap_label,
    resolve_tool_scopes,
    write_conflict,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lintro.models.core.claim import Claim

#: Config key a user edits to change a precedence decision.
PRECEDENCE_CONFIG_KEY: str = "execution.precedence"


class EdgeSource(StrEnum):
    """Why a derived edge exists.

    Attributes:
        PHASE: ``FIX`` -> ``FORMAT`` -> ``CHECK`` within one glob pattern.
        OVERLAP: Two writers whose run-scoped candidate sets intersect.
        OVERRIDE: A configured ``execution.precedence`` pair decided the
            direction of an overlap.
    """

    PHASE = auto()
    OVERLAP = auto()
    OVERRIDE = auto()


class OrderPlanningError(ValueError):
    """Planning failed and cannot degrade into a usable order.

    Raised only for a cycle a configured precedence override closed: the
    derived rules alone are acyclic, so this is always a user error with a
    named fix.
    """


#: Phases in the order they must run for one pattern.
PHASE_ORDER: tuple[Cap, ...] = (Cap.FIX, Cap.FORMAT, Cap.CHECK)

_PHASE_RANK: dict[Cap, int] = {cap: rank for rank, cap in enumerate(PHASE_ORDER)}


@dataclass(frozen=True)
class OrderEdge:
    """A single derived "runs before" constraint between two tools.

    Attributes:
        before: Tool that must run first.
        after: Tool that must run second.
        pattern: Glob pattern, or overlap scope label, that produced the
            constraint.
        before_capability: Phase ``before`` occupies on ``pattern``.
        after_capability: Phase ``after`` occupies on ``pattern``.
        source: Why the edge exists. Defaults to :attr:`EdgeSource.PHASE` so
            every existing construction keeps its meaning.
    """

    before: str
    after: str
    pattern: str
    before_capability: Cap
    after_capability: Cap
    source: EdgeSource = EdgeSource.PHASE

    @property
    def reason(self) -> str:
        """Describe the edge in one human-readable clause.

        Returns:
            A string such as ``"*.py: ruff(fix) -> black(format)"``, with
            ``", overlapping candidates"`` appended for a write-conflict edge
            and the config key named for a configured override.
        """
        clause = (
            f"{self.pattern}: {self.before}({self.before_capability}) -> "
            f"{self.after}({self.after_capability})"
        )
        if self.source is EdgeSource.OVERLAP:
            return f"{clause}, overlapping candidates"
        if self.source is EdgeSource.OVERRIDE:
            return f"{clause}, overlapping candidates ({PRECEDENCE_CONFIG_KEY})"
        return clause


@dataclass(frozen=True)
class FormatDemotion:
    """One tool's ``FORMAT`` capability stood down in favour of another.

    Attributes:
        winner: Tool that keeps ``FORMAT`` on the scope — the authority, and
            the last writer of it.
        loser: Tool whose ``FORMAT`` is demoted there. Its ``CHECK`` stays
            enabled, so it keeps reporting; it just stops writing.
        scope: The overlapping scope, as a pattern-shaped label.
        rule: Which precedence rule decided it.
    """

    winner: str
    loser: str
    scope: str
    rule: str

    @property
    def reason(self) -> str:
        """Describe the demotion in one human-readable clause.

        Returns:
            A string naming the winner, the loser, the scope and the rule.
        """
        return (
            f"{self.scope}: {self.winner} owns FORMAT; {self.loser}(format) "
            f"demoted ({self.rule}; override with {PRECEDENCE_CONFIG_KEY})"
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
        demotions: Format-owner demotions (#1744): one per tool whose
            ``FORMAT`` stood down on an overlapping scope.
    """

    tools: tuple[str, ...]
    edges: tuple[OrderEdge, ...]
    cycles: tuple[OrderCycle, ...]
    demotions: tuple[FormatDemotion, ...] = field(default=())


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


def _scopes_from_claims(
    claims_by_tool: Mapping[str, Sequence[Claim]],
) -> dict[str, ToolScope]:
    """Build unresolved scopes straight from declared claims.

    Used by callers that hand :func:`derive_order` claims directly rather
    than tool names — library callers and the unit tests that build synthetic
    claims for tools no registry knows. It deliberately does *not* consult the
    registry: a synthetic name has no definition, and resolving it would turn
    every such tool into an unknown project-scoped writer instead of the
    claims it was given. Production never reaches this path;
    :func:`build_order_report` always supplies scopes from
    :func:`~lintro.tools.core.tool_scopes.resolve_tool_scopes`, which can see
    ``partitionable`` and can tell an unresolvable name from a registered tool
    that declares nothing. Without a definition to read, the two therefore
    differ on exactly those two questions. No filesystem is touched either
    way, so overlap falls back to the conservative pattern comparison in
    :mod:`lintro.tools.core.tool_scopes`.

    Args:
        claims_by_tool: Claims keyed by tool name.

    Returns:
        One :class:`ToolScope` per tool, all with ``resolved=False``.
    """
    from lintro.enums.capability import MUTATING_CAPABILITIES

    scopes: dict[str, ToolScope] = {}
    for name, claims in claims_by_tool.items():
        patterns: set[str] = set()
        capabilities: set[Cap] = set()
        patternless = False
        declares_format = False
        for claim in claims:
            if Cap.FORMAT in claim.capabilities:
                declares_format = True
            if not (claim.capabilities & MUTATING_CAPABILITIES):
                continue
            capabilities |= set(claim.capabilities & MUTATING_CAPABILITIES)
            if claim.patterns:
                patterns.update(claim.patterns)
            else:
                patternless = True
        scopes[name] = ToolScope(
            tool=name,
            patterns=tuple(sorted(patterns)),
            mutating_capabilities=frozenset(capabilities),
            declares_format=declares_format,
            project_scoped=bool(capabilities) and patternless,
            known=True,
            resolved=False,
        )
    return scopes


def _mutating_phase(scope: ToolScope) -> Cap:
    """Return the phase a writer occupies, earliest first.

    Args:
        scope: The tool's run scope.

    Returns:
        ``FIX`` when the tool fixes, ``FORMAT`` when it only formats. An
        unknown writer is treated as ``FIX`` so it never claims format
        authority it never declared.
    """
    caps = scope.mutating_capabilities
    if Cap.FORMAT in caps and Cap.FIX not in caps:
        return Cap.FORMAT
    return Cap.FIX


#: Capability count attributed to a writer nothing is known about. Large
#: enough to sort it before every declared writer under the "fewer mutating
#: capabilities runs last" rule: a tool that never said what it does is given
#: no authority over one that did.
UNKNOWN_CAPABILITY_COUNT: int = 1_000_000


def _precedence_rank(scope: ToolScope) -> tuple[int, int, str]:
    """Rank a tool for write precedence; the larger rank runs last.

    The three components are the derived precedence rules in order: ``FIX``
    before ``FORMAT``, then the tool with fewer mutating capabilities last
    (the authority rule — a dedicated formatter outranks a multi-capability
    tool), then the tool id.

    Args:
        scope: The tool's run scope.

    Returns:
        A sort key. Larger means "runs later", which means "has authority".
    """
    if not scope.is_writer:
        # Never in a conflict edge; the rank only has to be deterministic.
        return (0, 0, scope.tool)
    if not scope.known:
        return (0, -UNKNOWN_CAPABILITY_COUNT, scope.tool)
    phase = 1 if _mutating_phase(scope) is Cap.FORMAT else 0
    return (phase, -len(scope.mutating_capabilities), scope.tool)


def _precedence_rule(left: ToolScope, right: ToolScope) -> str:
    """Name the rule that separates two writers.

    Args:
        left: One tool's scope.
        right: The other tool's scope.

    Returns:
        A short phrase naming the deciding rule.
    """
    left_rank, right_rank = _precedence_rank(left), _precedence_rank(right)
    if left_rank[0] != right_rank[0]:
        return "FIX runs before FORMAT"
    if left_rank[1] != right_rank[1]:
        return "fewer mutating capabilities"
    return "alphabetical tool id"


def _demotion_rule(
    left: ToolScope,
    right: ToolScope,
    *,
    winner: str,
    configured: bool,
) -> str:
    """Name the rule that actually chose the format owner.

    The winner comes from the linear extension, not from comparing the pair,
    and a phase edge can invert the pairwise answer — a tool that is ``FIX``
    on one pattern and ``FORMAT`` on another is ordered by the pattern they
    share, not by its rank. Naming the pairwise rule in that case would print
    a reason that argues for the other tool, so the label falls back to what
    did decide.

    Args:
        left: One tool's scope.
        right: The other tool's scope.
        winner: The tool the sequence put last.
        configured: Whether ``execution.precedence`` named this pair.

    Returns:
        A short phrase naming the deciding rule.
    """
    if configured:
        return "configured precedence"
    pairwise = max((left, right), key=_precedence_rank).tool
    if pairwise != winner:
        return "derived phase order"
    return _precedence_rule(left, right)


def _override_map(
    precedence: Sequence[Sequence[str]],
) -> dict[tuple[str, str], str]:
    """Index configured ``[winner, loser]`` pairs by the unordered pair.

    Args:
        precedence: Pairs from ``execution.precedence``. The first element
            has authority: it runs last, so its write survives, and it keeps
            ``FORMAT``.

    Returns:
        Mapping of the alphabetically sorted pair to the winning tool id.

    Raises:
        OrderPlanningError: When one pair and its reverse are both configured.
            Indexing by the unordered pair would otherwise let the second
            silently overwrite the first, and a contradiction that resolves to
            "whichever was written last" is the fail-open this rule exists to
            close — the same reason a configured cycle is rejected.
    """
    indexed: dict[tuple[str, str], str] = {}
    for pair in precedence:
        parts = [str(part).lower() for part in pair]
        # A malformed or self-referential pair is rejected by the config
        # loader before it gets here; skipping it keeps a direct API caller
        # from crashing the scheduler on input no config file can produce.
        if len(parts) != 2 or parts[0] == parts[1]:
            continue
        winner, loser = parts
        key = (min(winner, loser), max(winner, loser))
        previous = indexed.get(key)
        if previous is not None and previous != winner:
            raise OrderPlanningError(
                f"Configured tool precedence is contradictory: both "
                f"[{winner}, {loser}] and [{loser}, {winner}] appear in "
                f"{PRECEDENCE_CONFIG_KEY}. Keep one of the two pairs so the "
                "tools form an order rather than a loop.",
            )
        indexed[key] = winner
    return indexed


def _override_edges(
    scopes: Mapping[str, ToolScope],
    overrides: Mapping[tuple[str, str], str],
) -> list[OrderEdge]:
    """Turn configured precedence pairs into edges.

    Args:
        scopes: Run scopes keyed by tool name.
        overrides: Configured precedence, keyed by sorted pair.

    Returns:
        One edge per configured pair whose tools are both in this run: the
        loser runs first, the winner writes last.
    """
    edges: list[OrderEdge] = []
    for (one, other), winner in sorted(overrides.items()):
        if one not in scopes or other not in scopes:
            continue
        loser = one if winner == other else other
        edges.append(
            OrderEdge(
                before=loser,
                after=winner,
                pattern=overlap_label(scopes[loser], scopes[winner]),
                before_capability=_mutating_phase(scopes[loser]),
                after_capability=_mutating_phase(scopes[winner]),
                source=EdgeSource.OVERRIDE,
            ),
        )
    return edges


def _precedence_sequence(
    tools: Sequence[str],
    scopes: Mapping[str, ToolScope],
    settled: Sequence[OrderEdge],
) -> dict[str, int]:
    """Rank every tool in one total order consistent with the settled edges.

    Conflict edges are directed by this sequence rather than pair by pair.
    Pairwise comparison is not transitive once phase edges are in the graph —
    a tool can be ``FIX`` on one pattern and ``FORMAT`` on another — and an
    intransitive comparison closes cycles. A linear extension cannot: every
    derived edge points forward in it, so the combined graph is a DAG by
    construction and the only thing that can point backwards is a configured
    override, which is exactly the case that must fail loudly.

    Args:
        tools: Every tool in the graph.
        scopes: Run scopes keyed by tool name.
        settled: Edges the sequence must respect (phase edges and configured
            overrides).

    Returns:
        Mapping of tool name to its position in the sequence.
    """
    successors = _adjacency(tools, settled)
    indegree: dict[str, int] = dict.fromkeys(tools, 0)
    for node in successors:
        for nxt in successors[node]:
            indegree[nxt] += 1

    remaining = set(tools)
    sequence: dict[str, int] = {}
    while remaining:
        ready = sorted(
            (name for name in remaining if indegree[name] == 0),
            key=lambda name: _precedence_rank(scopes[name]),
        ) or sorted(remaining, key=lambda name: _precedence_rank(scopes[name]))
        chosen = ready[0]
        sequence[chosen] = len(sequence)
        remaining.discard(chosen)
        for nxt in successors[chosen]:
            if nxt in remaining:
                indegree[nxt] -= 1
    return sequence


def _conflict_edges(
    tools: Sequence[str],
    scopes: Mapping[str, ToolScope],
    phase_edges: Sequence[OrderEdge],
    overrides: Mapping[tuple[str, str], str],
) -> tuple[list[OrderEdge], list[FormatDemotion]]:
    """Derive write-conflict edges and the format demotions they imply.

    Args:
        tools: Every tool in the graph.
        scopes: Run scopes keyed by tool name.
        phase_edges: The ``FIX`` -> ``FORMAT`` -> ``CHECK`` edges already
            derived per pattern.
        overrides: Configured precedence, keyed by sorted pair.

    Returns:
        ``(edges, demotions)``. Both are sorted and deterministic.
    """
    override_edges = _override_edges(scopes, overrides)
    sequence = _precedence_sequence(
        tools,
        scopes,
        [*phase_edges, *override_edges],
    )
    settled = _reachability(_adjacency(tools, phase_edges))
    override_pairs = set(overrides)

    edges: list[OrderEdge] = list(override_edges)
    candidates: list[FormatDemotion] = []
    names = sorted(scopes)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            left, right = scopes[left_name], scopes[right_name]
            if not write_conflict(left, right):
                continue
            winner_name, loser_name = (
                (right_name, left_name)
                if sequence[right_name] > sequence[left_name]
                else (left_name, right_name)
            )
            pair = (left_name, right_name)
            label = overlap_label(left, right)
            already_ordered = (
                winner_name in settled[loser_name] or pair in override_pairs
            )
            if not already_ordered:
                # The pair is not separated yet, so the conflict edge is what
                # keeps the two out of one batch.
                edges.append(
                    OrderEdge(
                        before=loser_name,
                        after=winner_name,
                        pattern=label,
                        before_capability=_mutating_phase(scopes[loser_name]),
                        after_capability=_mutating_phase(scopes[winner_name]),
                        source=EdgeSource.OVERLAP,
                    ),
                )
            if left.declares_format and right.declares_format:
                rule = _demotion_rule(
                    left,
                    right,
                    winner=winner_name,
                    configured=pair in override_pairs,
                )
                candidates.append(
                    FormatDemotion(
                        winner=winner_name,
                        loser=loser_name,
                        scope=label,
                        rule=rule,
                    ),
                )
    # In a chain of three formatters only the tool that loses to nobody owns
    # FORMAT, so a record whose "winner" is itself demoted elsewhere names the
    # wrong owner and is dropped.
    demoted = {record.loser for record in candidates}
    demotions = [record for record in candidates if record.winner not in demoted]
    demotions.sort(key=lambda record: (record.loser, record.winner, record.scope))
    return edges, demotions


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


def _reject_configured_cycles(cycles: Sequence[OrderCycle]) -> None:
    """Fail planning when a configured override closed a cycle.

    The derived rules alone are acyclic, so a cycle holding an override edge
    can only have come from ``execution.precedence``. That is a user error
    with a named fix, and falling back to alphabetical would silently run the
    opposite of what the user asked for.

    Args:
        cycles: Cycles found in the derived graph.

    Raises:
        OrderPlanningError: When any cycle contains an override edge.
    """
    offending = [
        cycle
        for cycle in cycles
        if any(edge.source is EdgeSource.OVERRIDE for edge in cycle.edges)
    ]
    if not offending:
        return
    details = "; ".join(
        f"{' <-> '.join(cycle.tools)} on {', '.join(cycle.patterns)}"
        for cycle in offending
    )
    raise OrderPlanningError(
        f"Configured tool precedence is contradictory: {details}. "
        f"Remove or reverse one of the pairs in {PRECEDENCE_CONFIG_KEY} so "
        "the tools form an order rather than a loop.",
    )


def derive_order(
    claims_by_tool: Mapping[str, Sequence[Claim]],
    *,
    scopes: Mapping[str, ToolScope] | None = None,
    precedence: Sequence[Sequence[str]] = (),
    write_conflicts: bool = True,
) -> DerivedOrder:
    """Derive an execution order from declared claims.

    Args:
        claims_by_tool: Claims keyed by tool name. A tool with no claims
            (commitlint) is unordered and keeps its alphabetical position.
        scopes: Run-scoped write sets keyed by tool name. When omitted they
            are built from ``claims_by_tool`` alone, which makes overlap fall
            back to conservative pattern comparison.
        precedence: Configured ``[winner, loser]`` pairs. The winner has
            authority: it runs last and keeps ``FORMAT``.
        write_conflicts: Whether to derive write-conflict edges. ``False``
            for a read-only run, where nothing is rewritten and batching must
            stay exactly as it was.

    A configured precedence pair that closes a cycle fails planning with
    :class:`OrderPlanningError` rather than degrading, because the derived
    rules alone are acyclic and so the contradiction can only be the user's.

    Returns:
        The derived order together with the edges, cycles and format-owner
        demotions behind it.
    """
    tools = sorted(claims_by_tool)
    edges: list[OrderEdge] = []
    for pattern in _pattern_universe(claims_by_tool):
        edges.extend(_edges_for_pattern(claims_by_tool, pattern))

    demotions: list[FormatDemotion] = []
    if write_conflicts:
        resolved = (
            dict(scopes) if scopes is not None else _scopes_from_claims(claims_by_tool)
        )
        run_scopes = {name: resolved.get(name, ToolScope(tool=name)) for name in tools}
        conflict_edges, demotions = _conflict_edges(
            tools,
            run_scopes,
            edges,
            _override_map(precedence),
        )
        edges.extend(conflict_edges)

    # Two rules can derive the same constraint (a phase edge and an overlap
    # edge on the same pair); the graph only needs it once.
    edges = sorted(set(edges), key=_edge_sort_key)
    cycles = _find_cycles(tools, edges)
    _reject_configured_cycles(cycles)
    return DerivedOrder(
        tools=_linearize(tools, edges),
        edges=tuple(edges),
        cycles=cycles,
        demotions=tuple(demotions),
    )


def configured_precedence() -> tuple[tuple[str, str], ...]:
    """Read ``execution.precedence`` out of the resolved lintro config.

    Ordering must not be the thing an unusable config fails on, so a config
    that cannot be loaded contributes no override.

    Returns:
        ``(winner, loser)`` pairs, lowercased.
    """
    try:
        from lintro.config import get_config

        raw = getattr(get_config().execution, "precedence", None) or ()
    except (ImportError, OSError, ValueError, AttributeError, RuntimeError):
        return ()
    pairs: list[tuple[str, str]] = []
    for entry in raw:
        parts = [str(part).lower() for part in entry]
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return tuple(pairs)


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


def build_order_report(
    tool_names: Sequence[str],
    *,
    paths: Sequence[str] | None = None,
    exclude: str | None = None,
    include_venv: bool = False,
    diff_base: str | None = None,
    write_conflicts: bool = True,
) -> DerivedOrder:
    """Derive the execution order for a set of tools, with its reasoning.

    Args:
        tool_names: Tool names to order (case-insensitive).
        paths: Scan targets for this run. When given, write conflicts are
            computed from the files each mutating tool would actually be
            handed; when omitted they fall back to conservative pattern
            comparison.
        exclude: Comma-separated CLI exclude patterns, or ``None``.
        include_venv: Whether virtual-environment directories are in scope.
        diff_base: Resolved ``--diff`` base ref, or ``None``.
        write_conflicts: Whether to derive write-conflict edges. ``False``
            for a read-only run.

    Returns:
        The derived order together with the edges, cycles and demotions
        behind it.
    """
    normalized = [name.lower() for name in tool_names]
    scopes = (
        resolve_tool_scopes(
            normalized,
            paths=paths,
            exclude=exclude,
            include_venv=include_venv,
            diff_base=diff_base,
        )
        if write_conflicts
        else None
    )
    return derive_order(
        collect_tool_claims(normalized),
        scopes=scopes,
        precedence=configured_precedence(),
        write_conflicts=write_conflicts,
    )


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
