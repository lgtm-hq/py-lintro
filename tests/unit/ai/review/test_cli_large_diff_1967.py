"""Unit tests for CLI-transport large-diff handling (#1967)."""

from __future__ import annotations

import errno
import json
import subprocess  # nosec B404 - subprocess drives the CLI under test; shell=False
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderError
from lintro.ai.prompts.review import format_output_rules
from lintro.ai.providers.anthropic import AnthropicProvider
from lintro.ai.providers.cli_contracts import CliContract
from lintro.ai.providers.openai import OpenAIProvider
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.review.cli_limits import (
    CLI_DIFF_HARD_CEILING_BYTES,
    CLI_FINDINGS_RETRY_CAP,
    CLI_MAX_FINDINGS_PER_CALL,
    CLI_TRANSPORT_DIFF_TOKEN_BUDGET,
    assert_cli_diff_within_ceiling,
    is_output_exhaustion_error,
    measure_diff_size,
    resolve_cli_diff_budget,
    resolve_cli_findings_cap,
    tighter_findings_cap,
)
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.orchestrator import (
    # Deliberate private import: the retry loop is unit-tested at the helper
    # seam because driving it through run_review_async needs a full provider
    # + chunking stack for no extra coverage. Update this import when the
    # helper is renamed (#1967 review).
    _invoke_chunk_review,
    resolve_review_chunks,
    run_review_async,
)
from lintro.ai.token_budget import estimate_tokens
from tests.unit.ai.conftest import patch_cli_exec
from tests.unit.ai.providers.test_cli_capability_guard import _FakeTransport

_TEST_CONTRACT = CliContract(
    binary="fake",
    display_name="Fake",
    upgrade_hint="Upgrade the fake CLI.",
    version_floor=(2, 0, 0),
    required_flags=("--always",),
)


@pytest.fixture()
def _mock_claude_on_path() -> Iterator[None]:
    """Patch claude binary discovery for CLI transport tests."""
    with patch(
        "lintro.ai.providers.anthropic._find_claude",
        return_value="/usr/local/bin/claude",
    ):
        yield


@pytest.fixture()
def _mock_codex_on_path() -> Iterator[None]:
    """Patch codex binary discovery for CLI transport tests."""
    with patch(
        "lintro.ai.providers.openai._find_codex",
        return_value="/usr/local/bin/codex",
    ):
        yield


def _cli_json(*, result: str = "ok") -> str:
    return json.dumps(
        {
            "result": result,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.0,
        },
    )


def _jsonl_response(*, text: str = "ok") -> str:
    return "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": text},
                },
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            ),
        ],
    )


def _synthetic_diff(*, lines: int, files: int = 4) -> tuple[str, list[ChangedFile]]:
    """Build a multi-file unified diff with roughly *lines* content lines."""
    per_file = max(lines // files, 1)
    changed: list[ChangedFile] = []
    parts: list[str] = []
    for index in range(files):
        path = f"src/module_{index}.py"
        changed.append(
            ChangedFile(
                path=path,
                status="modified",
                additions=per_file,
                deletions=0,
            ),
        )
        # Pad lines so ~1.6k lines exceed the 24k-token CLI soft ceiling.
        pad = "payload_" + ("x" * 80)
        hunk_lines = "\n".join(
            f"+value_{index}_{row} = {row}  # {pad}" for row in range(per_file)
        )
        parts.append(
            f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            f"@@ -1,0 +1,{per_file} @@\n"
            f"{hunk_lines}\n",
        )
    return "".join(parts), changed


def test_measure_diff_size_counts_lines_bytes_and_tokens() -> None:
    """Diff size measurement keys off the effective pre-spawn text."""
    diff = "a\nb\nc"
    size = measure_diff_size(unified_diff=diff)

    assert_that(size.lines).is_equal_to(3)
    assert_that(size.bytes).is_equal_to(len(diff.encode("utf-8")))
    assert_that(size.tokens).is_equal_to(estimate_tokens(diff))


def test_resolve_cli_diff_budget_caps_context_window_remainder() -> None:
    """CLI soft ceiling wins over a huge context-window remainder."""
    budget = resolve_cli_diff_budget(
        context_window_budget=190_000,
        cli_max_diff_tokens=CLI_TRANSPORT_DIFF_TOKEN_BUDGET,
    )

    assert_that(budget).is_equal_to(CLI_TRANSPORT_DIFF_TOKEN_BUDGET)
    assert_that(budget).is_less_than(190_000)


def test_resolve_cli_diff_budget_keeps_smaller_context_remainder() -> None:
    """A tiny model window is not inflated to the CLI soft ceiling."""
    budget = resolve_cli_diff_budget(
        context_window_budget=1_000,
        cli_max_diff_tokens=CLI_TRANSPORT_DIFF_TOKEN_BUDGET,
    )

    assert_that(budget).is_equal_to(1_000)


def test_assert_cli_diff_within_ceiling_raises_actionable_error() -> None:
    """Hard ceiling refusals mention --paths and API transport."""
    oversized = "z" * (CLI_DIFF_HARD_CEILING_BYTES + 10)
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/huge.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=oversized,
        pr_metadata=None,
        repo_root="",
    )

    with pytest.raises(ReviewContextError) as exc_info:
        assert_cli_diff_within_ceiling(
            context=context,
            cli_max_diff_bytes=CLI_DIFF_HARD_CEILING_BYTES,
        )

    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.DIFF_TOO_LARGE)
    assert_that(str(exc_info.value)).contains("--paths")
    assert_that(str(exc_info.value)).contains("--transport api")


def test_cli_chunk_threshold_routes_large_diff_through_chunker() -> None:
    """A ~1.5k-line CLI-sized diff splits once the CLI soft budget applies."""
    unified_diff, changed = _synthetic_diff(lines=1_600, files=8)
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=changed,
        unified_diff=unified_diff,
        pr_metadata=None,
        repo_root="",
    )
    window_budget = 190_000
    cli_budget = resolve_cli_diff_budget(
        context_window_budget=window_budget,
        cli_max_diff_tokens=CLI_TRANSPORT_DIFF_TOKEN_BUDGET,
    )

    assert_that(estimate_tokens(unified_diff)).is_greater_than(cli_budget)
    assert_that(estimate_tokens(unified_diff)).is_less_than(window_budget)

    single = resolve_review_chunks(
        context=context,
        diff_budget=window_budget,
        classifications=[],
    )
    chunked = resolve_review_chunks(
        context=context,
        diff_budget=cli_budget,
        classifications=[],
    )

    assert_that(single).is_length(1)
    assert_that(len(chunked)).is_greater_than(1)


def test_resolve_cli_findings_cap_only_for_cli_transport() -> None:
    """API transport leaves findings uncapped; CLI applies the configured cap."""
    assert_that(
        resolve_cli_findings_cap(transport_is_cli=False, cli_max_findings_per_call=12),
    ).is_none()
    assert_that(
        resolve_cli_findings_cap(transport_is_cli=True, cli_max_findings_per_call=12),
    ).is_equal_to(12)


def test_tighter_findings_cap_reduces_but_stays_positive() -> None:
    """Output-exhaustion retries halve toward the retry floor."""
    assert_that(tighter_findings_cap(current=CLI_MAX_FINDINGS_PER_CALL)).is_equal_to(
        CLI_FINDINGS_RETRY_CAP,
    )
    assert_that(tighter_findings_cap(current=1)).is_equal_to(1)


def test_output_rules_bound_findings_per_call_when_capped() -> None:
    """Prompt contract states the findings cap used to avoid mid-JSON cuts."""
    rules = format_output_rules(checklist_count=4, max_findings=7)

    assert_that(rules).contains("**7**")
    assert_that(rules.lower()).contains("do not report the same problem twice")


def test_is_output_exhaustion_error_detects_known_signatures() -> None:
    """Output-cap retry only fires on exhaustion-shaped provider messages."""
    assert_that(
        is_output_exhaustion_error(
            "Claude CLI reported error: maximum output tokens exceeded",
        ),
    ).is_true()
    assert_that(is_output_exhaustion_error("authentication required")).is_false()
    # No output qualifier → could equally be an input context-window error.
    assert_that(
        is_output_exhaustion_error("Claude CLI: hit the token limit"),
    ).is_false()


async def test_claude_cli_argv_length_is_constant_in_prompt_size(
    _mock_claude_on_path: None,
) -> None:
    """Argv length stays O(1) as the prompt grows; the body rides on stdin."""
    provider = AnthropicProvider(transport=AITransport.CLI)
    small = "small prompt"
    large = "x" * 200_000

    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_cli_json(),
            stderr="",
        )
        await provider.complete(small, system="sys")
        await provider.complete(large, system="sys")

    completions = [
        call
        for call in mock_run.transport_calls
        if "--print" in call.cmd or "-p" in call.cmd
    ]
    assert_that(completions).is_length(2)
    first, second = completions
    assert_that(first.input_text).is_equal_to(small)
    assert_that(second.input_text).is_equal_to(large)
    assert_that(small).is_not_in(first.cmd)
    assert_that(large).is_not_in(second.cmd)
    assert_that(sum(len(token) for token in first.cmd)).is_equal_to(
        sum(len(token) for token in second.cmd),
    )
    assert_that(first.cmd).contains("--print")


async def test_codex_cli_uses_stdin_sentinel(
    _mock_codex_on_path: None,
) -> None:
    """Codex uses the ``-`` stdin sentinel so argv does not grow with the prompt."""
    provider = OpenAIProvider(transport=AITransport.CLI)
    large = "y" * 180_000

    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_jsonl_response(),
            stderr="",
        )
        await provider.complete(large, repo_root="/tmp/repo")

    call = mock_run.transport_calls[-1]
    assert_that(call.cmd[-1]).is_equal_to("-")
    assert_that(call.input_text).is_equal_to(large)
    assert_that(large).is_not_in(call.cmd)


async def test_cli_transport_maps_e2big_to_provider_error() -> None:
    """E2BIG spawn failures become actionable AIProviderError messages."""
    transport = _FakeTransport(
        binary_path="/usr/local/bin/fake",
        binary_name="Fake",
        install_hint="Install Fake.",
        contract=_TEST_CONTRACT,
    )
    with (
        patch_cli_exec(side_effect=OSError(errno.E2BIG, "Argument list too long")),
        pytest.raises(AIProviderError, match="E2BIG"),
    ):
        await transport.run(["/usr/local/bin/fake", "x" * 10], timeout=5.0)


async def test_run_review_rejects_cli_diff_above_hard_ceiling(
    tmp_path: Path,
) -> None:
    """CLI reviews above the hard ceiling raise an actionable context error."""
    unified_diff = "z" * (CLI_DIFF_HARD_CEILING_BYTES + 50)
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/huge.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=unified_diff,
        pr_metadata=None,
        repo_root=str(tmp_path),
    )
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    provider.capabilities.supports_sessions = False
    ai_config = AIConfig(
        enabled=True,
        review=True,
        transport=AITransport.CLI,
        cli_max_diff_bytes=CLI_DIFF_HARD_CEILING_BYTES,
    )

    with pytest.raises(ReviewContextError) as exc_info:
        await run_review_async(
            context=context,
            provider=provider,
            ai_config=ai_config,
            depth=1,
            checklist_items=[],
            checklist_text="",
            classifications=[],
        )

    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.DIFF_TOO_LARGE)


async def test_invoke_chunk_retries_on_cli_output_exhaustion(
    tmp_path: Path,
) -> None:
    """Output-cap failures retry once with a tighter findings budget."""
    chunk = ReviewChunk(
        id=1,
        files=["src/a.py"],
        diff="+x = 1\n",
        relationship="single-file",
    )
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="src/a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=chunk.diff,
        pr_metadata=None,
        repo_root=str(tmp_path),
    )
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    ai_config = AIConfig(enabled=True, review=True, transport=AITransport.CLI)
    budget = MagicMock()
    budget.check = MagicMock()

    ok_payload = {
        "summary": {
            "headline": "Adds a constant.",
            "walkthrough": [{"text": "Sets x.", "finding_ref": ""}],
        },
        "checklist": [],
        "findings": [],
        "verdict_reasoning": {
            "deciding_factor": "Nothing blocks.",
            "failure_mechanism": "n/a",
            "files_needing_attention": [],
        },
        "file_assessments": [{"file": "src/a.py", "overview": "Fine."}],
    }
    ok_response = AIResponse(
        content=json.dumps(ok_payload),
        model="claude-sonnet-4-6",
        provider=AIProvider.ANTHROPIC,
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.0,
    )
    calls: list[str] = []

    async def _fake_call_ai(**kwargs: object) -> AIResponse:
        prompt = str(kwargs.get("user_prompt", ""))
        calls.append(prompt)
        if len(calls) == 1:
            raise AIProviderError(
                "Claude CLI reported error: exceeded the maximum number of "
                "output tokens",
            )
        return ok_response

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        new=AsyncMock(side_effect=_fake_call_ai),
    ):
        response, _elapsed = await _invoke_chunk_review(
            chunk=chunk,
            context=context,
            provider=provider,
            ai_config=ai_config,
            checklist_text="",
            checklist_count=0,
            interaction_paths="",
            lint_results=None,
            extra_checklist="",
            strictness_section="",
            budget=budget,
            repo_root=str(tmp_path),
            use_one_shot=True,
            diff_budget=10_000,
            max_findings=CLI_MAX_FINDINGS_PER_CALL,
        )

    assert_that(calls).is_length(2)
    assert_that(calls[0]).contains(f"**{CLI_MAX_FINDINGS_PER_CALL}**")
    assert_that(calls[1]).contains(f"**{CLI_FINDINGS_RETRY_CAP}**")
    assert_that(response.content).contains("Adds a constant")
