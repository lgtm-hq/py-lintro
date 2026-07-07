"""Tests for the idiom-review tool plugin.

The AI provider is always mocked; no test requires live API access.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

import lintro.tools.definitions.idiom_review as plugin_module
from lintro.ai.exceptions import AIAuthenticationError
from lintro.parsers.idiom_review.idiom_review_issue import IdiomReviewIssue
from lintro.plugins.registry import ToolRegistry
from lintro.tools.definitions.idiom_review import IdiomReviewPlugin


def _write_py(tmp_path: Path) -> str:
    src = tmp_path / "sample.py"
    src.write_text("found = False\nfor x in items:\n    found = True\n")
    return str(tmp_path)


def test_plugin_is_registered() -> None:
    """The plugin registers under its hyphenated name."""
    tool = ToolRegistry.get("idiom-review")

    assert_that(tool).is_instance_of(IdiomReviewPlugin)


def test_definition_metadata() -> None:
    """Definition matches the documented contract."""
    definition = IdiomReviewPlugin().definition

    assert_that(definition.name).is_equal_to("idiom-review")
    assert_that(definition.priority).is_equal_to(95)
    assert_that(definition.can_fix).is_false()
    assert_that(definition.version_command).is_none()
    assert_that(definition.file_patterns).contains("*.py")
    assert_that(definition.default_options["enabled"]).is_false()


def test_disabled_by_default_skips(tmp_path: Path) -> None:
    """With no opt-in, check() is a no-op skip."""
    result = IdiomReviewPlugin().check([_write_py(tmp_path)], {})

    assert_that(result.skipped).is_true()
    assert_that(result.success).is_true()
    assert_that(result.skip_reason).contains("disabled by default")


def test_no_ai_provider_degrades_gracefully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no provider is available, the tool skips rather than fails."""
    monkeypatch.setattr(plugin_module, "is_ai_available", lambda: False)

    result = IdiomReviewPlugin().check([_write_py(tmp_path)], {"enabled": True})

    assert_that(result.skipped).is_true()
    assert_that(result.skip_reason).contains("No AI provider")


class _FakeEngine:
    """Engine stub returning canned findings without any AI call."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def review_file(
        self,
        *,
        file_path: str,
        source: str,  # noqa: ARG002
        language: str = "python",  # noqa: ARG002
    ) -> list[IdiomReviewIssue]:
        return [
            IdiomReviewIssue(
                file=file_path,
                line=1,
                message="Prefer any()",
                code="idiom/python/prefer-any",
                severity="WARNING",
                confidence="high",
            ),
            IdiomReviewIssue(
                file=file_path,
                line=2,
                message="Minor idiom",
                code="idiom/python/minor",
                severity="HINT",
                confidence="low",
            ),
        ]

    def review_duplication(
        self,
        _signatures: object,
    ) -> list[IdiomReviewIssue]:
        return []


def _patch_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_module, "is_ai_available", lambda: True)
    monkeypatch.setattr(plugin_module, "get_provider", lambda _cfg: object())
    monkeypatch.setattr(plugin_module, "IdiomReviewEngine", _FakeEngine)


def test_enabled_with_mocked_engine_reports_issues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opted-in with a mocked engine, findings surface in the result."""
    _patch_engine(monkeypatch)

    result = IdiomReviewPlugin().check(
        [_write_py(tmp_path)],
        {"enabled": True, "min_confidence": "low"},
    )

    assert_that(result.skipped).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)


def test_min_confidence_filters_low_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A high min_confidence drops lower-confidence findings."""
    _patch_engine(monkeypatch)

    result = IdiomReviewPlugin().check(
        [_write_py(tmp_path)],
        {"enabled": True, "min_confidence": "high"},
    )

    assert_that(result.issues_count).is_equal_to(1)
    issues = [i for i in (result.issues or []) if isinstance(i, IdiomReviewIssue)]
    assert_that([i.confidence for i in issues]).is_equal_to(["high"])


def test_ai_error_degrades_gracefully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider errors (e.g. depleted credits) skip instead of crashing."""
    monkeypatch.setattr(plugin_module, "is_ai_available", lambda: True)
    monkeypatch.setattr(plugin_module, "get_provider", lambda _cfg: object())

    class _RaisingEngine(_FakeEngine):
        def review_file(self, **_kwargs: object) -> list[IdiomReviewIssue]:
            raise AIAuthenticationError("no credits")

    monkeypatch.setattr(plugin_module, "IdiomReviewEngine", _RaisingEngine)

    result = IdiomReviewPlugin().check(
        [_write_py(tmp_path)],
        {"enabled": True},
    )

    assert_that(result.skipped).is_true()
    assert_that(result.skip_reason).contains("AI provider error")


def test_fix_raises_not_implemented() -> None:
    """idiom-review is a reporter and cannot fix."""
    assert_that(IdiomReviewPlugin().fix).raises(NotImplementedError).when_called_with(
        [],
        {},
    )


def test_invalid_mode_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo'd mode fails with a clear message instead of silent success."""
    _patch_engine(monkeypatch)

    result = IdiomReviewPlugin().check(
        [_write_py(tmp_path)],
        {"enabled": True, "mode": "bothh"},
    )

    assert_that(result.success).is_false()
    assert_that(result.skipped).is_false()
    assert_that(result.output).contains("invalid mode")
    assert_that(result.output).contains("bothh")


def test_invalid_max_files_string_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-numeric max_files fails cleanly instead of crashing the run."""
    _patch_engine(monkeypatch)

    result = IdiomReviewPlugin().check(
        [_write_py(tmp_path)],
        {"enabled": True, "max_files": "abc"},
    )

    assert_that(result.success).is_false()
    assert_that(result.output).contains("invalid max_files")


def test_zero_max_files_rejected_not_unlimited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_files=0 is a config error, not a request to review every file."""
    _patch_engine(monkeypatch)

    result = IdiomReviewPlugin().check(
        [_write_py(tmp_path)],
        {"enabled": True, "max_files": 0},
    )

    assert_that(result.success).is_false()
    assert_that(result.output).contains("invalid max_files")
