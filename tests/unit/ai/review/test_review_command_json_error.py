"""Wiring tests: review command emits the JSON error contract on failure."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.exceptions import AIAuthenticationError
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
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
    config.ai = {"enabled": True}
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
        "apply_cli_overrides",
        lambda resolved, **_kwargs: resolved,
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
    # Locate the envelope: lintro may log warnings ahead of it on stdout.
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload["error"]["kind"]).is_equal_to("auth_failed")
    assert_that(payload["error"]["provider"]).is_equal_to("anthropic")
    assert_that(payload["error"]["status"]).is_equal_to(401)
    assert_that(payload["error"]["retryable"]).is_false()
    assert_that(payload["error"]["provider_unavailable"]).is_true()


def test_json_unset_provider_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """An enabled review with no provider uses the error contract, not exit 1."""
    from lintro.ai.config import AIConfig
    from lintro.ai.review.error_contract import REVIEW_ERROR_EXIT_CODE

    config = MagicMock()
    config.ai = {"enabled": True, "review": True}
    config.review.depth = 1
    config.review.strictness = ReviewStrictness.BALANCED
    config.review.sensitivity = {}
    config.review.checklist_display = "off"
    config.review.force_semantic_chunking = False
    config.review.custom_agents = CustomAgentMode.DISABLED

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
        "apply_cli_overrides",
        lambda _resolved, **_kwargs: AIConfig.resolve_from_mapping(
            {"enabled": True, "review": True},
        ),
    )
    monkeypatch.setattr(
        review_module,
        "resolve_sensitivity_policy",
        lambda **_: MagicMock(),
    )

    result = CliRunner().invoke(review_command, ["--output", "json"])

    assert_that(result.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload["error"]["provider"]).is_equal_to("unset")
    assert_that(payload["error"]["message"]).contains("`ai.provider` in config")
    assert_that(payload["error"]["message"]).contains("LINTRO_AI_PROVIDER")
    assert_that(payload["error"]["message"]).contains("--provider")


def test_terminal_error_exits_two_not_one(patched_review: None) -> None:
    """Terminal output uses the same error exit code as JSON output.

    Exit ``1`` means "reviewed, found P1 issues". Reusing it for "could not
    review" is what let a CI wrapper report a green check for a review that never
    ran (#1826), so the two must stay distinguishable in every output format.

    Args:
        patched_review: Fixture neutralizing the review command's collaborators.
    """
    result = CliRunner().invoke(review_command, ["--output", "terminal"])

    assert_that(result.exit_code).is_equal_to(2)
