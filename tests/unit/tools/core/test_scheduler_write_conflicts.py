"""Overlapping mutators never share a batch (#2606).

Phase edges alone (``FIX`` -> ``FORMAT`` -> ``CHECK`` per glob pattern) left
two writers of one file in the same parallel batch, where the second write
drops the first tool's edit. These tests pin the rule that closes it: overlap
is computed from the files each mutating tool would actually be handed, the
direction is deterministic and user-overridable, and a read-only run batches
exactly as it did before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.models.core.claim import Claim
from lintro.tools.core.scheduler import (
    PRECEDENCE_CONFIG_KEY,
    EdgeSource,
    OrderPlanningError,
    build_order_report,
    configured_precedence,
    derive_order,
)
from lintro.tools.core.tool_manager import ToolManager
from lintro.tools.core.tool_scopes import (
    ToolScope,
    patterns_may_overlap,
    resolve_tool_scopes,
    write_conflict,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Build a small mixed-language tree for scope resolution.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        Path: Root of the tree.
    """
    (tmp_path / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "settings.toml").write_text("[a]\nb = 1\n", encoding="utf-8")
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    return tmp_path


def _batches(
    tools: list[str],
    *,
    tree: Path,
    action: Action = Action.FIX,
) -> list[list[str]]:
    """Batch a selection against a real scan root.

    Args:
        tools: Tool names to batch.
        tree: Scan root whose files decide overlap.
        action: Action the batches would be dispatched under.

    Returns:
        The derived batches.
    """
    return ToolManager().get_parallel_batches(
        tools,
        action=action,
        paths=[str(tree)],
    )


def _depth(batches: list[list[str]]) -> dict[str, int]:
    """Index each tool by the batch it landed in.

    Args:
        batches: Derived batches.

    Returns:
        Mapping of tool name to batch index.
    """
    return {name: index for index, batch in enumerate(batches) for name in batch}


def _claims(*specs: tuple[list[str], set[Cap]]) -> list[Claim]:
    """Build claims from ``(patterns, capabilities)`` pairs.

    Args:
        *specs: Pattern/capability pairs.

    Returns:
        The claims.
    """
    return [Claim(patterns=patterns, capabilities=caps) for patterns, caps in specs]


def test_overlapping_mutators_never_share_a_batch_but_disjoint_ones_do(
    tree: Path,
) -> None:
    """The central rule, in one assertion pair.

    ruff and typos both rewrite ``module.py``, so they are separated. ruff and
    taplo are both mutators with non-empty candidate sets that do not
    intersect — ``module.py`` against the two TOML files — so they keep running
    together. The second half has to be a *mutator* pair: a rule that
    over-serialised every writer (which is what #2452's bridge did) would
    still pass an assertion made with two read-only linters.

    Args:
        tree: Mixed-language scan root.
    """
    overlapping = _depth(_batches(["ruff", "typos"], tree=tree))
    disjoint = _batches(["ruff", "taplo"], tree=tree)

    assert_that(overlapping["ruff"]).is_not_equal_to(overlapping["typos"])
    assert_that(disjoint).is_length(1)
    assert_that(disjoint[0]).contains("ruff", "taplo")


def test_read_only_tools_are_never_split_by_a_mutating_run(tree: Path) -> None:
    """A ``CHECK``-only pair shares a batch even when the action mutates.

    Args:
        tree: Mixed-language scan root.
    """
    batches = _batches(["hadolint", "yamllint"], tree=tree)

    assert_that(batches).is_length(1)
    assert_that(batches[0]).contains("hadolint", "yamllint")


def test_a_contradictory_pair_and_its_reverse_fail_planning() -> None:
    """One pair and its reverse contradict as surely as a longer loop does.

    Indexing by the unordered pair would let the second silently overwrite the
    first, which is the fail-open the cycle rejection exists to close.
    """
    claims = {
        "one_fixer": _claims((["*.py"], {Cap.FIX})),
        "two_fixer": _claims((["*.py"], {Cap.FIX})),
    }
    precedence = [["one_fixer", "two_fixer"], ["two_fixer", "one_fixer"]]

    assert_that(derive_order).raises(OrderPlanningError).when_called_with(
        claims,
        precedence=precedence,
    )
    try:
        derive_order(claims, precedence=precedence)
    except OrderPlanningError as exc:
        message = str(exc)
    assert_that(message).contains("one_fixer")
    assert_that(message).contains("two_fixer")
    assert_that(message).contains(PRECEDENCE_CONFIG_KEY)


def test_repeating_the_same_pair_is_not_a_contradiction() -> None:
    """Saying the same thing twice is not a loop."""
    claims = {
        "one_fixer": _claims((["*.py"], {Cap.FIX})),
        "two_fixer": _claims((["*.py"], {Cap.FIX})),
    }

    derived = derive_order(
        claims,
        precedence=[["one_fixer", "two_fixer"], ["one_fixer", "two_fixer"]],
    )

    assert_that(list(derived.tools)).is_equal_to(["two_fixer", "one_fixer"])


def test_the_demotion_rule_names_what_actually_decided() -> None:
    """A phase edge that inverts the pairwise rank is labelled as such.

    ``one_tool`` is ``FIX`` on ``*.b`` and ``FORMAT`` on ``*.a``; ``two_tool``
    is ``FIX`` on ``*.a``. The two rank equally, so the pairwise rule would
    name the alphabetically last tool — but the phase edge on ``*.a`` puts
    ``two_tool`` first, so ``one_tool`` wins. The record must not print a
    reason that argues for the other tool.
    """
    derived = derive_order(
        {
            "one_tool": _claims(
                (["*.b"], {Cap.FIX}),
                (["*.a"], {Cap.FORMAT}),
            ),
            "two_tool": _claims((["*.a"], {Cap.FIX, Cap.FORMAT})),
        },
    )

    assert_that(derived.demotions).is_length(1)
    demotion = derived.demotions[0]
    assert_that(demotion.winner).is_equal_to("one_tool")
    assert_that(demotion.rule).is_equal_to("derived phase order")


def test_scheduling_twice_yields_the_same_plan(tree: Path) -> None:
    """Planning is idempotent: the same selection derives the same schedule.

    Args:
        tree: Mixed-language scan root.
    """
    selection = ["typos", "black", "ruff", "taplo", "mypy"]

    first = _batches(list(selection), tree=tree)
    second = _batches(list(selection), tree=tree)
    first_report = build_order_report(selection, paths=[str(tree)])
    second_report = build_order_report(selection, paths=[str(tree)])

    assert_that(second).is_equal_to(first)
    assert_that(second_report.tools).is_equal_to(first_report.tools)
    assert_that(second_report.edges).is_equal_to(first_report.edges)
    assert_that(second_report.demotions).is_equal_to(first_report.demotions)


def test_a_literal_claim_and_a_glob_claim_over_one_file_are_separated(
    tree: Path,
) -> None:
    """Clippy (``Cargo.toml``) and taplo (``*.toml``) reach the same file.

    Comparing the glob strings would say they never relate; comparing the
    files says they do.

    Args:
        tree: Mixed-language scan root.
    """
    depth = _depth(_batches(["clippy", "taplo"], tree=tree))

    assert_that(depth["clippy"]).is_not_equal_to(depth["taplo"])


def test_a_project_scoped_writer_is_separated_from_a_file_scoped_one(
    tree: Path,
) -> None:
    """A writer that expands to a project root conflicts with everything under it.

    Args:
        tree: Mixed-language scan root.
    """
    depth = _depth(_batches(["golangci_lint", "ruff"], tree=tree))

    assert_that(depth["golangci_lint"]).is_not_equal_to(depth["ruff"])


def test_a_broad_claim_and_a_narrow_claim_on_one_extension_conflict() -> None:
    """``*.py`` and ``test_*.py`` writers overlap even with no scan root.

    Phase edges keep the two pattern groups separate on purpose. A write
    conflict does not: ``test_a.py`` matches both.
    """
    derived = derive_order(
        {
            "broad_fixer": _claims((["*.py"], {Cap.FIX})),
            "narrow_fixer": _claims((["test_*.py"], {Cap.FIX})),
        },
    )

    overlap_edges = [
        edge for edge in derived.edges if edge.source is EdgeSource.OVERLAP
    ]
    assert_that(overlap_edges).is_length(1)
    assert_that(overlap_edges[0].before).is_equal_to("broad_fixer")
    assert_that(overlap_edges[0].after).is_equal_to("narrow_fixer")


def test_two_checkers_on_one_pattern_never_conflict() -> None:
    """Read-only capabilities produce no conflict edge, whatever they share."""
    derived = derive_order(
        {
            "one_checker": _claims((["*.py"], {Cap.CHECK})),
            "two_checker": _claims((["*.py"], {Cap.CHECK})),
        },
    )

    assert_that(derived.edges).is_empty()


def test_check_mode_batching_is_unchanged(tree: Path) -> None:
    """A read-only run derives no conflict edge, so its batches are the old ones.

    Args:
        tree: Mixed-language scan root.
    """
    selection = ["ruff", "typos", "black", "mypy"]

    check_batches = _batches(list(selection), tree=tree, action=Action.CHECK)

    assert_that(check_batches[0]).contains("ruff", "typos")
    # The mutating run of the same selection splits the pair the read-only run
    # keeps together, which is the whole difference between the two modes.
    fix_depth = _depth(_batches(list(selection), tree=tree, action=Action.FIX))
    assert_that(fix_depth["ruff"]).is_not_equal_to(fix_depth["typos"])


def test_the_format_owner_is_the_authority_not_merely_the_last_writer(
    tree: Path,
) -> None:
    """Black owns ``FORMAT`` on Python; ruff's ``FORMAT`` is demoted, its CHECK stays.

    Args:
        tree: Mixed-language scan root.
    """
    report = build_order_report(["ruff", "black"], paths=[str(tree)])

    assert_that(report.demotions).is_length(1)
    demotion = report.demotions[0]
    assert_that(demotion.winner).is_equal_to("black")
    assert_that(demotion.loser).is_equal_to("ruff")
    assert_that(demotion.reason).contains(PRECEDENCE_CONFIG_KEY)
    assert_that(list(report.tools)).is_equal_to(["ruff", "black"])


def test_an_overlap_edge_explains_itself_in_the_existing_vocabulary(
    tree: Path,
) -> None:
    """Explain output describes the new edge as an overlap, with its scope.

    Args:
        tree: Mixed-language scan root.
    """
    report = build_order_report(["ruff", "typos"], paths=[str(tree)])

    reasons = [
        edge.reason for edge in report.edges if edge.source is EdgeSource.OVERLAP
    ]
    assert_that(reasons).is_equal_to(
        ["*.py: ruff(fix) -> typos(fix), overlapping candidates"],
    )


def test_a_configured_override_reverses_the_derived_direction() -> None:
    """``execution.precedence`` decides who writes last, and says so."""
    claims = {
        "one_fixer": _claims((["*.py"], {Cap.FIX})),
        "two_fixer": _claims((["*.py"], {Cap.FIX})),
    }

    derived = derive_order(claims)
    overridden = derive_order(claims, precedence=[["one_fixer", "two_fixer"]])

    assert_that(list(derived.tools)).is_equal_to(["one_fixer", "two_fixer"])
    assert_that(list(overridden.tools)).is_equal_to(["two_fixer", "one_fixer"])
    assert_that(
        [edge.source for edge in overridden.edges],
    ).contains(EdgeSource.OVERRIDE)


def test_a_contradictory_override_fails_planning_with_an_actionable_message() -> None:
    """Configured cycles never degrade into an alphabetical guess."""
    claims = {
        "one_fixer": _claims((["*.py"], {Cap.FIX})),
        "two_fixer": _claims((["*.py"], {Cap.FIX})),
        "three_fixer": _claims((["*.py"], {Cap.FIX})),
    }
    precedence = [
        ["one_fixer", "two_fixer"],
        ["two_fixer", "three_fixer"],
        ["three_fixer", "one_fixer"],
    ]

    assert_that(derive_order).raises(OrderPlanningError).when_called_with(
        claims,
        precedence=precedence,
    )
    try:
        derive_order(claims, precedence=precedence)
    except OrderPlanningError as exc:
        message = str(exc)
    assert_that(message).contains("one_fixer")
    assert_that(message).contains("two_fixer")
    assert_that(message).contains(PRECEDENCE_CONFIG_KEY)


def test_an_unknown_plugin_gets_a_batch_of_its_own(tree: Path) -> None:
    """Nothing is known about a name the registry cannot resolve, so it runs alone.

    Args:
        tree: Mixed-language scan root.
    """
    depth = _depth(_batches(["ruff", "not-a-registered-tool"], tree=tree))

    assert_that(depth["ruff"]).is_not_equal_to(depth["not-a-registered-tool"])


def test_candidates_are_canonical_paths_not_spellings(tmp_path: Path) -> None:
    """A symlinked scan root resolves to the same file identity as the real one.

    Overlap is a set intersection, so two spellings of one file have to
    canonicalise to one string or the conflict is missed.

    Args:
        tmp_path: pytest temporary directory.
    """
    real = tmp_path / "real"
    real.mkdir()
    (real / "module.py").write_text("x = 1\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    # Two writers, because a lone writer cannot conflict and the candidate
    # walk is deliberately skipped for it.
    through_real = resolve_tool_scopes(["ruff", "black"], paths=[str(real)])["ruff"]
    through_link = resolve_tool_scopes(["ruff", "black"], paths=[str(link)])["ruff"]

    assert_that(str(link)).is_not_equal_to(str(real))
    assert_that(through_link.candidates).is_equal_to(through_real.candidates)
    assert_that(write_conflict(through_real, through_link)).is_true()


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (".env.*", "*.env"),
        ("Dockerfile.*", "*.py"),
        ("*.py", "test_*.py"),
        ("Cargo.toml", "*.toml"),
        ("*", "*.py"),
    ],
)
def test_patterns_that_can_match_one_file_are_never_called_disjoint(
    left: str,
    right: str,
) -> None:
    """The no-paths fallback answers "provably disjoint?", never "equal?".

    ``.env.env`` matches ``.env.*`` and ``*.env``; ``Dockerfile.py`` matches
    ``Dockerfile.*`` and ``*.py``. An extension test cannot bound a pattern
    whose extension is itself a wildcard, so an unknown extension has to mean
    "may overlap" — reading it as a value to compare turns "cannot tell" into
    "provably disjoint", and a false negative here is a lost write.

    Args:
        left: One glob pattern.
        right: The other glob pattern.
    """
    assert_that(patterns_may_overlap(left, right)).is_true()
    assert_that(patterns_may_overlap(right, left)).is_true()


@pytest.mark.parametrize(
    ("left", "right"),
    [("*.py", "*.toml"), ("*.py", "*.pyi"), ("Cargo.toml", "Cargo.lock")],
)
def test_provably_disjoint_patterns_stay_disjoint(left: str, right: str) -> None:
    """Erring towards True must not collapse into "everything overlaps".

    Args:
        left: One glob pattern.
        right: The other glob pattern.
    """
    assert_that(patterns_may_overlap(left, right)).is_false()


def test_a_missing_scope_entry_is_not_a_free_pass() -> None:
    """A claimed writer absent from a supplied scope map still conflicts.

    Defaulting a missing entry to an empty scope would read as "declares
    nothing", which exempts a mutating tool from every conflict edge — the
    exact race the rule closes.
    """
    claims = {
        "one_fixer": _claims((["*.py"], {Cap.FIX})),
        "two_fixer": _claims((["*.py"], {Cap.FIX})),
    }
    # ``two_fixer`` is missing; a defaults-only substitute for it would make
    # it a non-writer and drop the pair's only edge.
    partial = {
        "one_fixer": ToolScope(
            tool="one_fixer",
            patterns=("*.py",),
            mutating_capabilities=frozenset({Cap.FIX}),
        ),
    }

    derived = derive_order(claims, scopes=partial)

    assert_that(
        [edge for edge in derived.edges if edge.source is EdgeSource.OVERLAP],
    ).is_length(1)


def test_an_override_accepts_the_cli_spelling_of_a_tool_id() -> None:
    """``golangci-lint`` in config must reach the id ``golangci_lint``.

    Registry ids are inconsistent about the separator — ``golangci_lint`` and
    ``astro-check`` are both real — so neither "always underscore" nor "always
    hyphen" canonicalises a name. It is resolved against the ids in the run
    instead, which cannot invent a tool that is not there.
    """
    derived = derive_order(
        {
            "golangci_lint": _claims((["*.go"], {Cap.FIX})),
            "ruff": _claims((["*.py"], {Cap.FIX})),
        },
        precedence=[["golangci-lint", "ruff"]],
    )

    assert_that(
        [edge.source for edge in derived.edges],
    ).contains(EdgeSource.OVERRIDE)
    assert_that(list(derived.tools)).is_equal_to(["ruff", "golangci_lint"])


def test_a_narrowed_scan_scope_still_finds_the_conflict(tree: Path) -> None:
    """Scoping the run to one file keeps the writers of that file apart.

    ``--diff`` and an explicit file argument both narrow what the run hands
    each tool, and overlap is resolved from those narrowed sets. Narrowing
    must not erase a conflict on a file both tools still reach.

    Args:
        tree: Mixed-language scan root.
    """
    only_python = [str(tree / "module.py")]

    depth = _depth(
        ToolManager().get_parallel_batches(
            ["ruff", "typos"],
            action=Action.FIX,
            paths=only_python,
        ),
    )

    assert_that(depth["ruff"]).is_not_equal_to(depth["typos"])


def test_a_narrowed_scan_scope_frees_tools_it_separates(tree: Path) -> None:
    """Two writers scoped to different files may share a batch.

    Args:
        tree: Mixed-language scan root.
    """
    batches = ToolManager().get_parallel_batches(
        ["ruff", "taplo"],
        action=Action.FIX,
        paths=[str(tree / "module.py"), str(tree / "settings.toml")],
    )

    assert_that(batches).is_length(1)
    assert_that(batches[0]).contains("ruff", "taplo")


def test_an_unreadable_config_says_the_override_was_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failing open on a bad config must not be silent.

    A user whose override was dropped would otherwise see the derived order
    and believe it was theirs.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    import lintro.config as config_module

    def _raise() -> NoReturn:
        """Stand in for a configuration that cannot be read.

        Raises:
            ValueError: Always.
        """
        raise ValueError("unparseable")

    monkeypatch.setattr(config_module, "get_config", _raise)
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        assert_that(configured_precedence()).is_empty()
    finally:
        logger.remove(sink)

    assert_that("\n".join(messages)).contains(PRECEDENCE_CONFIG_KEY)
