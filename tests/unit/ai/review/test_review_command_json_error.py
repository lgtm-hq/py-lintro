"""Wiring tests: review command emits the JSON error contract on failure."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.exceptions import AIAuthenticationError
from lintro.ai.review import preparation as preparation_module
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
    from lintro.ai.config import AIConfig

    config = MagicMock()
    config.ai = {"enabled": True, "review": True, "provider": "anthropic"}
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
        preparation_module,
        "collect_review_context",
        lambda **_: MagicMock(changed_files=[]),
    )
    monkeypatch.setattr(preparation_module, "classify_changed_files", lambda _: [])
    monkeypatch.setattr(preparation_module, "get_all_checklist_items", lambda **_: [])
    monkeypatch.setattr(preparation_module, "select_checklist_items", lambda **_: [])
    monkeypatch.setattr(
        preparation_module,
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
        "resolve_effective_ai_config",
        lambda _mapping, **_kwargs: AIConfig.resolve_from_mapping(
            {"enabled": True, "review": True, "provider": "anthropic"},
        ),
    )
    monkeypatch.setattr(review_module, "get_provider", lambda _, **_kwargs: provider)
    monkeypatch.setattr(
        preparation_module,
        "resolve_sensitivity_policy",
        lambda **_: MagicMock(),
    )

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise AIAuthenticationError(
            "Anthropic authentication failed: Error code: 401 - authentication_error",
        )

    monkeypatch.setattr(preparation_module, "run_review", _raise)


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
        preparation_module,
        "collect_review_context",
        lambda **_: MagicMock(changed_files=[]),
    )
    monkeypatch.setattr(preparation_module, "classify_changed_files", lambda _: [])
    monkeypatch.setattr(preparation_module, "get_all_checklist_items", lambda **_: [])
    monkeypatch.setattr(preparation_module, "select_checklist_items", lambda **_: [])
    monkeypatch.setattr(
        preparation_module,
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
        "resolve_effective_ai_config",
        lambda _mapping, **_kwargs: AIConfig.resolve_from_mapping(
            {"enabled": True, "review": True},
        ),
    )
    monkeypatch.setattr(
        preparation_module,
        "resolve_sensitivity_policy",
        lambda **_: MagicMock(),
    )

    result = CliRunner().invoke(review_command, ["--output", "json"])

    assert_that(result.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload["error"]["kind"]).is_equal_to("provider_unavailable")
    assert_that(payload["error"]["provider"]).is_equal_to("unset")
    assert_that(payload["error"]["provider_unavailable"]).is_true()
    assert_that(payload["error"]["message"]).contains("`ai.provider` in config")
    assert_that(payload["error"]["message"]).contains("LINTRO_AI_PROVIDER")
    assert_that(payload["error"]["message"]).contains("--provider")


def test_json_unset_provider_beats_context_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset provider wins over a failing context collection.

    ``lintro review`` used to collect the git diff before constructing a
    provider. An invalid base or a non-repo cwd then hid the required-provider
    migration error behind ``ReviewContextError``.
    """
    from lintro.ai.config import AIConfig
    from lintro.ai.review.enums.review_context_error_code import (
        ReviewContextErrorCode,
    )
    from lintro.ai.review.error_contract import REVIEW_ERROR_EXIT_CODE
    from lintro.ai.review.exceptions import ReviewContextError

    config = MagicMock()
    config.ai = {"enabled": True, "review": True}
    config.review.depth = 1
    config.review.strictness = ReviewStrictness.BALANCED
    config.review.sensitivity = {}
    config.review.checklist_display = "off"
    config.review.force_semantic_chunking = False
    config.review.custom_agents = CustomAgentMode.DISABLED

    def _raise_context(**_: object) -> None:
        raise ReviewContextError(
            "not a git repository",
            code=ReviewContextErrorCode.NOT_GIT_REPO,
        )

    monkeypatch.setattr(review_module, "require_ai", lambda: None)
    monkeypatch.setattr(review_module, "get_config", lambda: config)
    monkeypatch.setattr(
        preparation_module,
        "collect_review_context",
        _raise_context,
    )
    monkeypatch.setattr(
        review_module,
        "resolve_effective_ai_config",
        lambda _mapping, **_kwargs: AIConfig.resolve_from_mapping(
            {"enabled": True, "review": True},
        ),
    )

    result = CliRunner().invoke(review_command, ["--output", "json"])

    assert_that(result.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload["error"]["kind"]).is_equal_to("provider_unavailable")
    assert_that(payload["error"]["provider"]).is_equal_to("unset")
    assert_that(payload["error"]["provider_unavailable"]).is_true()
    assert_that(payload["error"]["message"]).contains("`ai.provider` in config")
    assert_that(payload["error"]["message"]).does_not_contain("not a git repository")


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


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("gh-command-failed", "gh pr diff failed: unavailable"),
        ("gh-command-failed", "gh pr diff timed out"),
        ("gh-command-failed", "gh pr view failed: HTTP 401"),
        ("gh-json-invalid", "Failed to parse gh pr view JSON"),
        ("gh-metadata-invalid", "Invalid required PR metadata"),
        ("diff-desync", "Changed files listed but unified diff is empty"),
        ("no-parseable-diff", "No parseable diff sections"),
    ],
)
@pytest.mark.parametrize("output_format", ["json", "terminal"])
def test_preparation_context_failure_discloses_error(
    patched_review: None,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    message: str,
    output_format: str,
) -> None:
    """A collection failure is an error, even when its text resembles a provider."""
    from lintro.ai.review.exceptions import ReviewContextError

    def fail(**_: object) -> None:
        """Raise the context collection failure under test."""
        raise ReviewContextError(message, code=code) from TimeoutError("timed out")

    monkeypatch.setattr(preparation_module, "collect_review_context", fail)
    result = CliRunner().invoke(review_command, ["--output", output_format])
    assert_that(result.exit_code).is_equal_to(2)
    assert_that(result.output).contains(message)
    if output_format == "json":
        error = json.loads(result.output[result.output.index("{") :])["error"]
        assert_that(error).contains_entry(
            kind="context_unavailable",
            code=code,
            message=message,
            provider_unavailable=False,
            retryable=False,
        )


@pytest.mark.parametrize("output_format", ["json", "terminal"])
def test_no_changes_discloses_skipped_files(
    patched_review: None,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    """An excluded selection exits successfully without claiming it was reviewed."""
    from lintro.ai.review.enums.file_skip_reason import FileSkipReason
    from lintro.ai.review.exceptions import ReviewContextError
    from lintro.ai.review.models.skipped_file import SkippedFile

    def empty(**_: object) -> None:
        """Simulate collection retaining its deliberate exclusion records."""
        raise ReviewContextError(
            "No changes",
            code="no-changes",
            skipped_files=(
                SkippedFile(path="generated.py", reason=FileSkipReason.CONFIG_EXCLUDED),
            ),
        )

    monkeypatch.setattr(preparation_module, "collect_review_context", empty)
    result = CliRunner().invoke(review_command, ["--output", output_format])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains(
        "No reviewable changes, no review ran.",
        "generated.py",
    )
    if output_format == "json":
        payload = json.loads(result.output[result.output.index("{") :])
        assert_that(payload).contains_entry(outcome="no_changes", review_ran=False)
        assert_that(payload).does_not_contain("metadata", "findings_coverage_complete")
        assert_that(payload["skipped_files"][0]["reason"]).is_equal_to(
            "config_excluded",
        )


def test_execution_context_failure_keeps_context_kind(
    patched_review: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The execution diff ceiling carries the same context error contract."""
    from lintro.ai.review.exceptions import ReviewContextError

    def too_large(*_: object, **__: object) -> None:
        """Refuse the diff at the execution boundary."""
        raise ReviewContextError("CLI diff ceiling exceeded", code="diff-too-large")

    monkeypatch.setattr(preparation_module, "run_review", too_large)
    result = CliRunner().invoke(review_command, ["--output", "json"])
    assert_that(result.exit_code).is_equal_to(2)
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload["error"]).contains_entry(
        kind="context_unavailable",
        code="diff-too-large",
    )


@pytest.mark.parametrize("excluded", [False, True])
def test_empty_collected_selection_exits_zero_without_reviewing(
    patched_review: None,
    monkeypatch: pytest.MonkeyPatch,
    excluded: bool,
) -> None:
    """Real collection retains exclusions through NO_CHANGES into the CLI skip."""
    from pathlib import Path
    from types import SimpleNamespace

    from lintro.ai.review.context import collection
    from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
    from lintro.ai.review.models.changed_file import ChangedFile
    from lintro.ai.review.models.review_context import ReviewContext

    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=(
            [
                ChangedFile(
                    path="a.py",
                    status=ChangedFileStatus.MODIFIED,
                    additions=1,
                    deletions=0,
                ),
            ]
            if excluded
            else []
        ),
        unified_diff="diff --git a/a.py b/a.py\n+++ b/a.py\n+a\n" if excluded else "",
    )
    monkeypatch.setattr(
        preparation_module,
        "collect_review_context",
        collection.collect_review_context,
    )
    monkeypatch.setattr(collection, "_ensure_git_repo", lambda: None)
    monkeypatch.setattr(
        collection,
        "_run_git",
        lambda **_: SimpleNamespace(stdout=str(Path.cwd())),
    )
    monkeypatch.setattr(collection, "_collect_branch_context", lambda **_: context)
    args = ["--base", "main", "--output", "json"]
    if excluded:
        args.extend(["--path", "missing/"])
    result = CliRunner().invoke(review_command, args)
    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output[result.output.index("{") :])
    assert_that(payload).contains_entry(outcome="no_changes", review_ran=False)
    if excluded:
        assert_that(payload["skipped_files"][0]).contains_entry(
            path="a.py",
            reason="path_filter",
        )
    else:
        assert_that(payload["skipped_files"]).is_empty()
