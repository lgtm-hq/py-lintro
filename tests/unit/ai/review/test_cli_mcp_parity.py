"""CLI-versus-MCP parity for review preparation (issues #2298, #2300).

Both surfaces used to prepare a review independently (epic #1972, problem 2).
#2300 replaced that with one shared path — ``ReviewRunRequest`` →
:func:`~lintro.ai.review.preparation.prepare_review` → ``PreparedReview`` →
:func:`~lintro.ai.review.preparation.execute_review` — so this module drives
``lintro review`` through the Click runner and the MCP ``_execute_review``
entry point over the *same* workspace, captures what each one hands to
``execute_review``, and asserts the two prepared reviews are **equal**.

What may still differ is execution policy, not preparation: the fields of
:class:`~lintro.ai.review.preparation.ReviewExecutionPolicy` the CLI sets and
MCP leaves at its defaults. That set is named explicitly below and may only
shrink.
"""

from __future__ import annotations

import subprocess  # nosec B404 - fixed git argv against a temp repo
from collections.abc import Callable, Iterator
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.preparation import (
    DEFAULT_EXECUTION_POLICY,
    PreparedReview,
    ReviewExecutionPolicy,
    execute_review,
)
from lintro.ai.review.progress import NullReviewProgress
from lintro.ai.review.session import ReviewSessionOptions
from lintro.cli import cli
from lintro.config.review_config import ReviewSynthesisConfig
from lintro.mcp.toolkits import review as mcp_review

#: Execution-policy fields only the CLI populates. Each is adapter policy the
#: MCP surface deliberately does not have: terminal progress, the CLI's
#: ``--context-window`` flag, resume state, and the CLI's cost-cap gate.
#:
#: #2300 removed ``custom_agents`` and ``run_builtin_checklist`` from this set:
#: both are shared preparation now, resolved from the request's custom-agent
#: mode. This is the complete ``ReviewExecutionPolicy`` field set, asserted as
#: such below, so it is a ratchet in both directions: it shrinks only when a
#: policy field stops existing, and a new adapter-only knob fails the test
#: instead of quietly widening the gap.
CLI_ONLY_POLICY_FIELDS: frozenset[str] = frozenset(
    {
        "context_window_override",
        "progress",
        "prior_state",
        "force_full",
        "enforce_cost_cap",
    },
)

#: ``PreparedReview`` fields whose values cannot be compared across surfaces:
#: ``context_collection_seconds`` is wall-clock (asserted as a non-negative
#: float on each surface, never compared between them). It is excluded from
#: ``PreparedReview`` equality for the same reason, and asserted explicitly
#: rather than silently skipped.
UNCOMPARABLE_PREPARED_FIELDS: frozenset[str] = frozenset(
    {"context_collection_seconds"},
)


class _FakeProvider:
    """Minimal provider stand-in with a stable identity.

    Attributes:
        name: Provider name reported to the orchestrator.
        model_name: Model identifier reported to the orchestrator.
    """

    name = "anthropic"
    model_name = "test-model"


@pytest.fixture(autouse=True)
def _isolate_config_cache() -> Iterator[None]:
    """Clear the global config singleton around every test in this module.

    The tests chdir into a temp repo and exercise real resolution paths, so a
    populated singleton would leak the temp workspace into unrelated suites.

    Yields:
        None: The cache is cleared on both sides of the test body.
    """
    from lintro.config import config_loader

    config_loader.clear_config_cache()
    yield
    config_loader.clear_config_cache()


def _git(*args: str, cwd: Path) -> None:
    """Run one fixed git command in ``cwd``.

    Args:
        *args: Git arguments.
        cwd: Directory to run in.
    """
    subprocess.run(  # nosec B603 B607 - fixed git argv against a temp repo
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _metadata() -> ReviewMetadata:
    """Build placeholder metadata for the stubbed review result.

    Returns:
        Metadata for a completed one-chunk run.
    """
    return ReviewMetadata(
        model="test-model",
        provider="anthropic",
        context_window=128_000,
        depth=1,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        chunks_reviewed=1,
    )


def _stub_result() -> ReviewResult:
    """Return the finding-free result both surfaces receive.

    Returns:
        A successful review result with no findings.
    """
    return ReviewResult(metadata=_metadata(), summary="Review complete.")


def _write_parity_workspace(tmp_path: Path, *, exclude_paths: str = "") -> Path:
    """Create a git workspace both surfaces can review.

    The three changed files mirror the golden fixture's shapes (a text
    modification, a binary file, and a rename) so parity is asserted over a
    non-trivial diff rather than a one-line change.

    Args:
        tmp_path: Pytest temporary directory.
        exclude_paths: Optional ``ai.exclude_paths`` glob written into the
            workspace config. Empty leaves the production default (no globs).

    Returns:
        Resolved workspace root on a branch ahead of ``main``.
    """
    workspace = tmp_path.resolve()
    # A real agent file, so "both surfaces resolve custom agents identically"
    # is asserted against discovery that finds something. Without it the CLI's
    # configured mode and a built-in-only surface would look the same.
    agents_dir = workspace / ".lintro" / "review-agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "parity.md").write_text(
        "---\nname: parity\ndescription: Parity fixture agent\n"
        'include: ["*.py"]\n---\n\n'
        "Report nothing; this agent exists so discovery is non-empty.\n",
        encoding="utf-8",
    )
    excludes = f"  exclude_paths:\n    - '{exclude_paths}'\n" if exclude_paths else ""
    (workspace / ".lintro-config.yaml").write_text(
        "ai:\n"
        "  enabled: true\n"
        "  review: true\n"
        "  provider: anthropic\n"
        "  model: test-model\n"
        "  max_cost_usd: 1.0\n"
        f"{excludes}"
        "review:\n"
        "  synthesis:\n"
        "    enabled: true\n"
        "    max_findings: 7\n",
        encoding="utf-8",
    )
    _git("init", "--initial-branch", "main", cwd=workspace)
    _git("config", "user.email", "test@example.com", cwd=workspace)
    _git("config", "user.name", "Test", cwd=workspace)
    (workspace / "session.py").write_text("x = 1\n", encoding="utf-8")
    (workspace / "token_utils.py").write_text("def decode():\n    return {}\n", "utf-8")
    (workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    _git("add", "-A", cwd=workspace)
    _git("commit", "-m", "base", cwd=workspace)
    _git("checkout", "-b", "feature", cwd=workspace)
    (workspace / "session.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (workspace / "token_utils.py").rename(workspace / "tokens.py")
    (workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff" * 16)
    _git("add", "-A", cwd=workspace)
    _git("commit", "-m", "change", cwd=workspace)
    return workspace


def _capture_calls(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exclude_paths: str = "",
) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    """Drive both surfaces over one workspace and capture their calls.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
        exclude_paths: Optional ``ai.exclude_paths`` glob for the workspace.

    Returns:
        Tuple of the CLI and MCP ``execute_review`` call mappings — each with
        the ``prepared`` review, the ``policy`` and the ``provider`` — and the
        configs every ``get_provider`` call was made with, in call order.
    """
    import lintro.ai.availability as availability
    import lintro.ai.providers as providers
    import lintro.ai.review.preparation as preparation
    from lintro.config import config_loader

    workspace = _write_parity_workspace(tmp_path, exclude_paths=exclude_paths)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(availability, "is_ai_available", lambda: True)
    # One factory serves both surfaces so the recorded call count and configs
    # come from production asking, not from the patch style. It cannot observe
    # caching inside the real get_provider, which is replaced here.
    provider_configs: list[Any] = []

    def _build_provider(config: Any, **_kwargs: Any) -> _FakeProvider:
        """Return a fresh provider and record the config it was built from.

        Args:
            config: Effective AI config the adapter resolved.
            **_kwargs: Remaining ``get_provider`` arguments, unused here.

        Returns:
            A new provider stand-in per call.
        """
        provider_configs.append(config)
        return _FakeProvider()

    monkeypatch.setattr(providers, "get_provider", _build_provider)
    loaded = config_loader.load_config(config_path=workspace / ".lintro-config.yaml")
    monkeypatch.setattr(config_loader, "get_config", lambda: loaded)

    mcp_calls: list[dict[str, Any]] = []
    cli_calls: list[dict[str, Any]] = []

    def _record(sink: list[dict[str, Any]]) -> Callable[..., ReviewResult]:
        """Build an ``execute_review`` stand-in recording into ``sink``.

        Args:
            sink: List the captured call mapping is appended to.

        Returns:
            A callable with the ``execute_review`` signature shape.
        """

        def _execute_review(
            prepared: PreparedReview,
            *,
            provider: Any,
            policy: ReviewExecutionPolicy = DEFAULT_EXECUTION_POLICY,
        ) -> ReviewResult:
            sink.append(
                {"prepared": prepared, "provider": provider, "policy": policy},
            )
            return _stub_result()

        return _execute_review

    # MCP resolves ``execute_review`` from the shared module at call time; the
    # CLI binds it at import. Each surface is therefore patched where it looks,
    # so neither borrows the other's stub.
    monkeypatch.setattr(preparation, "execute_review", _record(mcp_calls))
    mcp_review._execute_review(arguments={"base": "main"}, workspace=workspace)

    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch("lintro.cli_utils.commands.review.get_config", return_value=loaded),
        patch(
            "lintro.cli_utils.commands.review.execute_review",
            side_effect=_record(cli_calls),
        ),
        patch("lintro.cli_utils.commands.review.render_review_output"),
        patch(
            "lintro.cli_utils.commands.review.get_provider",
            side_effect=_build_provider,
        ),
        patch("lintro.cli_utils.commands.review._execute_advisory", return_value=[]),
    ):
        cli_result = runner.invoke(cli, ["review", "--base", "main"])

    assert_that(cli_result.exit_code).is_equal_to(0)
    assert_that(cli_calls).is_length(1)
    assert_that(mcp_calls).is_length(1)
    return cli_calls[0], mcp_calls[0], provider_configs


def test_cli_and_mcp_prepare_equal_reviews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both surfaces produce an equal ``PreparedReview`` for one workspace.

    This is the Phase 3 (#2300) contract in one assertion: preparation is a
    single shared path, so a change that made one adapter resolve depth,
    checklist, sensitivity, custom agents, the lint digest, or the effective
    AI config differently reddens here.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call, _ = _capture_calls(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert_that(cli_call["prepared"]).is_equal_to(mcp_call["prepared"])
    # The workspace ships one custom review agent, so this is discovery that
    # found something on both surfaces rather than two empty tuples matching.
    assert_that(cli_call["prepared"].custom_agents).is_not_empty()
    # Equality skips the wall-clock field by design; both must still report a
    # real measurement rather than defaulting to nothing.
    for call in (cli_call, mcp_call):
        seconds = call["prepared"].context_collection_seconds
        assert_that(seconds).is_instance_of(float)
        assert_that(seconds).is_greater_than_or_equal_to(0.0)


def test_every_prepared_field_is_compared_or_named_uncomparable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``PreparedReview`` field escapes the parity assertion unnoticed.

    A field added with ``compare=False`` would silently leave the equality
    above, so the excluded set is pinned to the documented allowlist.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, _, _ = _capture_calls(tmp_path=tmp_path, monkeypatch=monkeypatch)

    excluded = {
        field.name for field in fields(cli_call["prepared"]) if not field.compare
    }

    assert_that(excluded).is_equal_to(set(UNCOMPARABLE_PREPARED_FIELDS))


def test_cli_and_mcp_build_the_same_review_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both surfaces collect an identical review context for one workspace.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call, _ = _capture_calls(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    cli_context = cli_call["prepared"].context
    mcp_context = mcp_call["prepared"].context
    assert_that(asdict(cli_context)).is_equal_to(asdict(mcp_context))
    paths = {file.path for file in cli_context.changed_files}
    assert_that(paths).contains("session.py", "tokens.py", "logo.png")


def test_only_the_allowlisted_policy_fields_differ_between_the_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI/MCP divergence is exactly the documented execution policy.

    MCP runs on the default policy, and every field the CLI sets differently
    is on the allowlist. A new adapter-only knob on either surface fails here
    rather than silently widening the gap Phase 3 closed.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call, _ = _capture_calls(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert_that(mcp_call["policy"]).is_equal_to(DEFAULT_EXECUTION_POLICY)
    cli_policy = cli_call["policy"]
    differing = {
        field.name
        for field in fields(cli_policy)
        if getattr(cli_policy, field.name)
        != getattr(DEFAULT_EXECUTION_POLICY, field.name)
    }
    assert_that(differing).is_subset_of(CLI_ONLY_POLICY_FIELDS)
    # ...and the CLI really does populate a policy of its own, so a surface
    # that silently fell back to the defaults cannot pass this test.
    assert_that(cli_policy).is_not_equal_to(DEFAULT_EXECUTION_POLICY)
    assert_that(differing).contains("progress", "prior_state")
    assert_that({field.name for field in fields(cli_policy)}).is_equal_to(
        set(CLI_ONLY_POLICY_FIELDS),
    )


def test_each_surface_builds_its_own_provider_from_an_equivalent_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each adapter constructs a provider of its own from the same effective config.

    What this pins: both adapters ask for a provider (twice in total, from
    equivalent effective config) and each ``execute_review`` call receives its
    own instance, so a change that stopped one surface constructing a provider
    reddens here. What it cannot see: ``get_provider`` is replaced on both
    adapter bindings, so a singleton or session cache *inside* the real
    ``lintro.ai.providers.get_provider`` would not surface — Phase 5 (#2302)
    needs its own pin for the shared-lifetime end state.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call, provider_configs = _capture_calls(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert_that(provider_configs).is_length(2)
    mcp_config, cli_config = provider_configs
    assert_that(cli_config.provider).is_equal_to(mcp_config.provider)
    assert_that(cli_config.model).is_equal_to(mcp_config.model)
    assert_that(cli_call["provider"]).is_not_same_as(mcp_call["provider"])


def test_exclude_paths_now_shapes_the_context_on_both_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ai.exclude_paths`` is honoured by MCP as well as the CLI.

    Before #2300 this was the one context axis the surfaces disagreed on: the
    CLI forwarded ``exclude_globs=list(ai_config.exclude_paths)`` into
    ``collect_review_context`` and MCP's own collection passed nothing, so an
    excluded file was dropped and recorded as skipped on one surface and
    reviewed on the other. Shared preparation reads the exclusion from the
    resolved AI config, so the operator's setting now applies to both — closed
    in the CLI's direction, deliberately.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call, _ = _capture_calls(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        exclude_paths="tokens.py",
    )
    cli_context = cli_call["prepared"].context
    mcp_context = mcp_call["prepared"].context

    for context in (cli_context, mcp_context):
        assert_that({file.path for file in context.changed_files}).does_not_contain(
            "tokens.py",
        )
        assert_that({skipped.path for skipped in context.skipped_files}).contains(
            "tokens.py",
        )


#: Where every :class:`ReviewSessionOptions` field the adapter path fills comes
#: from: ``"prepared"`` for shared preparation, ``"policy"`` for adapter-owned
#: execution policy. ``timeout`` and ``stop`` are deliberately absent — neither
#: adapter sets them through ``execute_review`` (an explicit ``--timeout`` is
#: applied to the AI config during preparation instead), so they are asserted
#: to arrive at the object's own defaults.
SESSION_OPTION_SOURCES: dict[str, tuple[str, str]] = {
    "ai_config": ("prepared", "ai_config"),
    "depth": ("prepared", "depth"),
    "checklist_items": ("prepared", "checklist_items"),
    "checklist_text": ("prepared", "checklist_text"),
    "classifications": ("prepared", "classifications"),
    "lint_results": ("prepared", "lint_digest"),
    "sensitivity": ("prepared", "sensitivity"),
    "force_semantic_chunking": ("prepared", "force_semantic_chunking"),
    "custom_agents": ("prepared", "custom_agents"),
    "run_builtin_checklist": ("prepared", "run_builtin_checklist"),
    "workspace_root": ("prepared", "workspace_root"),
    "context_collection_seconds": ("prepared", "context_collection_seconds"),
    "synthesis": ("prepared", "synthesis"),
    "context_window_override": ("policy", "context_window_override"),
    "progress": ("policy", "progress"),
    "prior_state": ("policy", "prior_state"),
    "force_full": ("policy", "force_full"),
    "enforce_cost_cap": ("policy", "enforce_cost_cap"),
}

#: Session fields no adapter sets on this path. They must still arrive at the
#: default :class:`ReviewSessionOptions` documents.
UNSET_SESSION_OPTION_FIELDS: frozenset[str] = frozenset({"timeout", "stop"})


def test_execute_review_forwards_every_prepared_and_policy_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every session option the adapter path fills reaches ``run_review``.

    ``execute_review`` is the one place a prepared review and an execution
    policy become a :class:`ReviewSessionOptions`, and it packs the object
    field by field. A field dropped there would strand a resolved setting at
    its default with no surface reporting the difference, so this asserts the
    full mapping — every field, from its named source — and that the only
    fields left at a default are the two no adapter sets.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import lintro.ai.review.preparation as preparation

    cli_call, _, _ = _capture_calls(tmp_path=tmp_path, monkeypatch=monkeypatch)
    # Every value is moved off its default so a dropped field cannot match by
    # coincidence with the default it would fall back to.
    prepared = replace(
        cli_call["prepared"],
        depth=3,
        checklist_text="1. [security] Packed?",
        force_semantic_chunking=True,
        run_builtin_checklist=False,
        lint_digest="lint digest",
        workspace_root=tmp_path / "elsewhere",
        context_collection_seconds=1.5,
        synthesis=ReviewSynthesisConfig(enabled=True),
    )
    policy = ReviewExecutionPolicy(
        progress=NullReviewProgress(),
        context_window_override=4242,
        prior_state=ReviewState(),
        force_full=True,
        enforce_cost_cap=False,
    )
    sources = {"prepared": prepared, "policy": policy}
    captured: list[ReviewSessionOptions] = []

    def _record_run_review(
        context: Any,
        *,
        options: ReviewSessionOptions,
    ) -> ReviewResult:
        """Record the options ``execute_review`` packed.

        Args:
            context: Review context the orchestrator would run over.
            options: The packed session options.

        Returns:
            A stub review result.
        """
        del context
        captured.append(options)
        return _stub_result()

    monkeypatch.setattr(preparation, "run_review", _record_run_review)
    # The stand-in stands in for a provider the orchestrator never calls here.
    provider: Any = _FakeProvider()

    execute_review(prepared, provider=provider, policy=policy)

    assert_that(captured).is_length(1)
    options = captured[0]
    mismatched = sorted(
        name
        for name, (source, attribute) in SESSION_OPTION_SOURCES.items()
        if getattr(options, name) != getattr(sources[source], attribute)
    )
    defaults = {field.name: field.default for field in fields(ReviewSessionOptions)}
    left_at_default = sorted(
        name
        for name in UNSET_SESSION_OPTION_FIELDS
        if getattr(options, name) != defaults[name]
    )

    assert_that(options.provider).is_same_as(provider)
    assert_that(mismatched).is_empty()
    assert_that(left_at_default).is_empty()
    assert_that(
        sorted([*SESSION_OPTION_SOURCES, *UNSET_SESSION_OPTION_FIELDS]),
    ).is_equal_to(
        sorted(
            field.name
            for field in fields(ReviewSessionOptions)
            if field.name != "provider"
        ),
    )
