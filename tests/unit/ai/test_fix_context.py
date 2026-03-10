"""Tests for _build_fix_context.

Covers full file context for small files, threshold, and token budget.
"""

from __future__ import annotations

import threading

from assertpy import assert_that

from lintro.ai.fix import (
    _call_provider,
    _generate_single_fix,
    generate_fixes,
)
from lintro.ai.retry import with_retry
from tests.unit.ai.conftest import MockAIProvider, MockIssue

# ---------------------------------------------------------------------------
# P3-3: Full file context for small files
# ---------------------------------------------------------------------------


def test_full_file_context_for_small_file(tmp_path):
    """Small files should send full content as context (lines 1-N)."""
    source = tmp_path / "small.py"
    source.write_text("x = 1\ny = 2\nz = 3\n")

    issue = MockIssue(
        file=str(source),
        line=2,
        code="E501",
        message="Line too long",
    )

    provider = MockAIProvider()
    generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
    )

    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    # Full file sent: context window should span the entire file
    assert_that(prompt).contains("lines 1-3")
    assert_that(prompt).contains("x = 1")
    assert_that(prompt).contains("z = 3")


def test_full_file_skipped_when_file_exceeds_threshold(tmp_path):
    """Files over full_file_threshold should use windowed context."""
    # Create a file with 50 lines but set threshold to 5
    source = tmp_path / "big.py"
    source.write_text("\n".join(f"line_{i}" for i in range(1, 51)) + "\n")

    issue = MockIssue(
        file=str(source),
        line=25,
        code="E501",
        message="Line too long",
    )

    provider = MockAIProvider()
    retrying_call = with_retry(max_retries=0)(_call_provider)

    _generate_single_fix(
        issue,
        provider,
        "ruff",
        {},
        threading.Lock(),
        tmp_path,
        2048,
        retrying_call,
        full_file_threshold=5,  # File has 50 lines, above threshold
    )

    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    # Should NOT contain "lines 1-50" (full file); uses windowed context
    assert_that(prompt).does_not_contain("lines 1-50")
    # Should contain a windowed range around line 25
    assert_that(prompt).contains("line_25")


def test_full_file_skipped_when_over_token_budget(tmp_path):
    """Full file that exceeds token budget should fall back to windowed context."""
    # Create a file with 20 lines, set a tight token budget so full-file
    # context is rejected and windowed context is used instead.
    source = tmp_path / "medium.py"
    lines = [f"line_{i} = {i}" for i in range(1, 21)]
    source.write_text("\n".join(lines) + "\n")

    issue = MockIssue(
        file=str(source),
        line=10,
        code="E501",
        message="Line too long",
    )

    provider = MockAIProvider()
    retrying_call = with_retry(max_retries=0)(_call_provider)

    _generate_single_fix(
        issue,
        provider,
        "ruff",
        {},
        threading.Lock(),
        tmp_path,
        2048,
        retrying_call,
        max_prompt_tokens=10,  # Very tight budget, full file won't fit
    )

    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    # Should use windowed context, not full file (1-20)
    assert_that(prompt).does_not_contain("lines 1-20")
    # Should contain the target line
    assert_that(prompt).contains("line_10")
