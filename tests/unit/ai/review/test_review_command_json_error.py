"""Wiring tests: review command emits the JSON error contract on failure."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.exceptions import AIAuthenticationError
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.cli_utils.commands import review as review_module
from lintro.cli_utils.commands.review import review_command


@pytest.fixture
def patched_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the review command's collaborators up to ``run_review``.

    Every dependency before the provider call is neutralized so the test can
    drive a single failure mode (``run_review`` raising) through the real
    ``--output json`` error branch.
    """
    config = MagicMock()
    config.ai.enabled = True
    config.review.depth = 1
    config.review.strictness = ReviewStrictness.BALANCED
    config.review.sensitivity = {}
    config.review.checklist_display = "off"
    config.review.force_semantic_chunking = False

    provider = MagicMock()
    provider.name = "anthropic"

    monkeypatch.setattr(review_module, "require_ai", lambda: None)
    monkeypatch.setattr(review_module, "get_config", lambda: config)
    monkeypatch.setattr(
        review_module,
        "collect_review_context",
        lambda **_: MagicMock(changed_files=[]),
    )
    monkeypatch.setattr(review_module, "classify_changed_files", lambda _: [])
    monkeypatch.setattr(review_module, "get_all_checklist_items", lambda **_: [])
    monkeypatch.setattr(review_module, "select_checklist_items", lambda **_: [])
    monkeypatch.setattr(
        review_module,
        "format_checklist_for_prompt",
        lambda **_: ("", {}),
    )
    monkeypatch.setattr(review_module, "build_prompt_question_map", lambda **_: {})
    monkeypatch.setattr(
        review_module,
        "resolve_checklist_display",
        lambda **_: ChecklistDisplay.OFF,
    )
    monkeypatch.setattr(
        review_module,
        "apply_transport_override",
        lambda ai_config, _transport: ai_config,
    )
    monkeypatch.setattr(review_module, "get_provider", lambda _, **_kwargs: provider)
    monkeypatch.setattr(
        review_module,
        "resolve_sensitivity_policy",
        lambda **_: MagicMock(),
    )

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise AIAuthenticationError(
            "Anthropic authentication failed: Error code: 401 - authentication_error",
        )

    monkeypatch.setattr(review_module, "run_review", _raise)


def test_json_error_emits_envelope_and_exits_two(
    patched_review: None,
) -> None:
    """A provider failure under --output json prints the envelope and exits 2."""
    runner = CliRunner()
    result = runner.invoke(review_command, ["--output", "json"])

    assert_that(result.exit_code).is_equal_to(2)
    payload = json.loads(result.output)
    assert_that(payload["error"]["kind"]).is_equal_to("auth_failed")
    assert_that(payload["error"]["provider"]).is_equal_to("anthropic")
    assert_that(payload["error"]["status"]).is_equal_to(401)
    assert_that(payload["error"]["retryable"]).is_false()
