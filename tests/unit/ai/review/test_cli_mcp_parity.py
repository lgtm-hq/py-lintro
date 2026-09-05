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
#: ``provider`` is a per-surface provider instance and
#: ``context_collection_seconds`` is wall-clock. Both are asserted separately
#: below rather than skipped outright.
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


def _write_parity_workspace(tmp_path: Path) -> Path:
    """Create a git workspace both surfaces can review.

    The three changed files mirror the golden fixture's shapes (a text
    modification, a binary file, and a rename) so parity is asserted over a
    non-trivial diff rather than a one-line change.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Resolved workspace root on a branch ahead of ``main``.
    """
    workspace = tmp_path.resolve()
    (workspace / ".lintro-config.yaml").write_text(
        "ai:\n"
        "  enabled: true\n"
        "  review: true\n"
        "  provider: anthropic\n"
        "  model: test-model\n"
        "  max_cost_usd: 1.0\n"
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Drive both surfaces over one workspace and capture their calls.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Tuple of the CLI and MCP ``run_review`` call mappings, each with the
        positional review context under the ``context`` key.
    """
    import lintro.ai.availability as availability
    import lintro.ai.providers as providers
    import lintro.ai.review.orchestrator as orchestrator
    from lintro.config import config_loader

    workspace = _write_parity_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(availability, "is_ai_available", lambda: True)
    monkeypatch.setattr(
        providers,
        "get_provider",
        lambda config, **_kwargs: _FakeProvider(),
    )
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
            return_value=_FakeProvider(),
        ),
        patch("lintro.cli_utils.commands.review._execute_advisory", return_value=[]),
    ):
        cli_result = runner.invoke(cli, ["review", "--base", "main"])

    assert_that(cli_result.exit_code).is_equal_to(0)
    assert_that(cli_calls).is_length(1)
    assert_that(mcp_calls).is_length(1)
    return cli_calls[0], mcp_calls[0]


def test_cli_and_mcp_build_the_same_review_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both surfaces collect an identical review context for one workspace.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call = _capture_calls(tmp_path=tmp_path, monkeypatch=monkeypatch)

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
    cli_call, mcp_call = _capture_calls(tmp_path=tmp_path, monkeypatch=monkeypatch)
    cli_kwargs = {key: value for key, value in cli_call.items() if key != "context"}
    mcp_kwargs = {key: value for key, value in mcp_call.items() if key != "context"}

    shared = (set(cli_kwargs) & set(mcp_kwargs)) - UNCOMPARABLE_SHARED_KWARGS
    assert_that(shared).is_not_empty()
    for key in sorted(shared):
        assert_that(cli_kwargs[key]).described_as(key).is_equal_to(mcp_kwargs[key])


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
    cli_call, mcp_call = _capture_calls(tmp_path=tmp_path, monkeypatch=monkeypatch)
    cli_kwargs = set(cli_call) - {"context"}
    mcp_kwargs = set(mcp_call) - {"context"}

    assert_that(cli_kwargs - mcp_kwargs).is_equal_to(set(CLI_ONLY_KWARGS))
    assert_that(mcp_kwargs - cli_kwargs).is_empty()


def test_both_surfaces_build_their_own_provider_with_the_same_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider instances differ per surface but resolve to the same identity.

    Provider lifetime moves into the run session in Phase 5 (#2302); this
    records the pre-move state so that change is visible.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    cli_call, mcp_call = _capture_calls(tmp_path=tmp_path, monkeypatch=monkeypatch)

    assert_that(cli_call["provider"]).is_not_same_as(mcp_call["provider"])
    assert_that(cli_call["provider"].name).is_equal_to(mcp_call["provider"].name)
    assert_that(cli_call["provider"].model_name).is_equal_to(
        mcp_call["provider"].model_name,
    )
