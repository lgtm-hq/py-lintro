"""CLI-versus-MCP parity for review preparation (issue #2298).

Both surfaces prepare a review independently today (epic #1972, problem 2).
This module drives ``lintro review`` through the Click runner and the MCP
``_execute_review`` entry point over the *same* workspace, captures the
``run_review`` call each one makes, and asserts the shared inputs are equal
field for field — with the divergences named in an explicit allowlist rather
than tolerated implicitly.

Phase 3 (#2300) replaces the duplicated preparation with one shared path. When
it does, this test must keep passing and the allowlist may only shrink.
"""

from __future__ import annotations

import subprocess  # nosec B404 - fixed git argv against a temp repo
from collections.abc import Callable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.cli import cli
from lintro.mcp.toolkits import review as mcp_review

#: ``run_review`` keyword arguments only the CLI passes. Each entry is
#: adapter policy the MCP surface deliberately does not have: terminal
#: progress, user-defined agents, resume state, and the CLI's own cost-cap
#: and context-window flags.
CLI_ONLY_KWARGS: frozenset[str] = frozenset(
    {
        "context_window_override",
        "progress",
        "custom_agents",
        "run_builtin_checklist",
        "prior_state",
        "force_full",
        "enforce_cost_cap",
    },
)

#: Shared keyword arguments whose *values* cannot be compared directly:
#: ``provider`` is a per-surface provider instance (asserted by identity and
#: construction below) and ``context_collection_seconds`` is wall-clock
#: (asserted as a non-negative float on each surface, never compared across
#: them). Neither is silently skipped.
UNCOMPARABLE_SHARED_KWARGS: frozenset[str] = frozenset(
    {
        "provider",
        "context_collection_seconds",
    },
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
        Tuple of the CLI and MCP ``run_review`` call mappings — each with the
        positional review context under the ``context`` key — and the configs
        every ``get_provider`` call was made with, in call order.
    """
    import lintro.ai.availability as availability
    import lintro.ai.providers as providers
    import lintro.ai.review.orchestrator as orchestrator
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
        """Build a ``run_review`` stand-in recording into ``sink``.

        Args:
            sink: List the captured call mapping is appended to.

        Returns:
            A callable with the ``run_review`` signature shape.
        """

        def _run_review(context: Any, **kwargs: Any) -> ReviewResult:
            sink.append({"context": context, **kwargs})
            return _stub_result()

        return _run_review

    monkeypatch.setattr(orchestrator, "run_review", _record(mcp_calls))
    mcp_review._execute_review(arguments={"base": "main"}, workspace=workspace)

    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch("lintro.cli_utils.commands.review.get_config", return_value=loaded),
        patch(
            "lintro.cli_utils.commands.review.run_review",
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

    assert_that(asdict(cli_call["context"])).is_equal_to(asdict(mcp_call["context"]))
    paths = {file.path for file in cli_call["context"].changed_files}
    assert_that(paths).contains("session.py", "tokens.py", "logo.png")


def test_cli_and_mcp_pass_equal_shared_run_review_kwargs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every shared ``run_review`` kwarg carries the same value on both surfaces.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call, _ = _capture_calls(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    cli_kwargs = {key: value for key, value in cli_call.items() if key != "context"}
    mcp_kwargs = {key: value for key, value in mcp_call.items() if key != "context"}

    shared = (set(cli_kwargs) & set(mcp_kwargs)) - UNCOMPARABLE_SHARED_KWARGS
    assert_that(shared).is_not_empty()
    for key in sorted(shared):
        assert_that(cli_kwargs[key]).described_as(key).is_equal_to(mcp_kwargs[key])

    # The wall-clock kwarg cannot be compared across surfaces, but both must
    # still report a real measurement rather than defaulting to nothing.
    for kwargs in (cli_kwargs, mcp_kwargs):
        seconds = kwargs["context_collection_seconds"]
        assert_that(seconds).is_instance_of(float)
        assert_that(seconds).is_greater_than_or_equal_to(0.0)


def test_only_the_allowlisted_kwargs_differ_between_the_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI/MCP kwarg divergence is exactly the documented allowlist.

    A new adapter-only kwarg on either surface fails here rather than silently
    widening the gap Phase 3 has to close.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call, _ = _capture_calls(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    cli_kwargs = set(cli_call) - {"context"}
    mcp_kwargs = set(mcp_call) - {"context"}

    assert_that(cli_kwargs - mcp_kwargs).is_equal_to(set(CLI_ONLY_KWARGS))
    assert_that(mcp_kwargs - cli_kwargs).is_empty()


def test_each_surface_builds_its_own_provider_from_an_equivalent_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each adapter constructs a provider of its own from the same effective config.

    What this pins: both adapters ask for a provider (twice in total, from
    equivalent effective config) and each ``run_review`` call receives its own
    instance, so a Phase 3 change that stopped one surface constructing a
    provider reddens here. What it cannot see: ``get_provider`` is replaced on
    both adapter bindings, so a singleton or session cache *inside* the real
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


def test_exclude_paths_is_the_one_context_axis_the_surfaces_disagree_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ai.exclude_paths`` shapes the CLI's context and is ignored by MCP.

    The equal-context test above runs with the production default (no globs),
    where the divergence is invisible. This pins it: the CLI forwards
    ``exclude_globs=list(ai_config.exclude_paths)`` into
    ``collect_review_context`` and MCP's ``_collect_context`` passes nothing, so
    an excluded file is dropped and recorded as skipped on one surface and
    reviewed on the other. Phase 3 (#2300) must close this deliberately, not by
    accident — and this test says which direction it moved.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call, _ = _capture_calls(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        exclude_paths="tokens.py",
    )
    cli_paths = {file.path for file in cli_call["context"].changed_files}
    mcp_paths = {file.path for file in mcp_call["context"].changed_files}

    assert_that(cli_paths).does_not_contain("tokens.py")
    assert_that(mcp_paths).contains("tokens.py")
    assert_that(
        {skipped.path for skipped in cli_call["context"].skipped_files},
    ).contains("tokens.py")
    assert_that(mcp_call["context"].skipped_files).is_empty()
