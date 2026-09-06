"""Tests for the AI interface facade (issue #724 PR 2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

import lintro.ai.interface as interface
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport, ConfigSource
from lintro.ai.interface import (
    _warn_ai_fix_disabled,
    ai_exit_code_override,
    render_ai_status,
    run_ai_layer,
)
from lintro.ai.models import AIResult
from lintro.ai.resolved_ai_config import ResolvedAIConfig
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult

# ---------------------------------------------------------------------------
# _warn_ai_fix_disabled
# ---------------------------------------------------------------------------


def test_warn_ai_fix_disabled_warns_only_for_check_when_fix_requested_and_ai_disabled():
    """Warn when action is CHECK, ai_fix=True, and AI disabled."""
    logger = MagicMock()

    _warn_ai_fix_disabled(
        action=Action.CHECK,
        ai_fix=True,
        ai_lint_enabled=False,
        logger=logger,
    )

    assert_that(logger.console_output.call_count).is_equal_to(1)
    warning_text = logger.console_output.call_args[0][0]
    assert_that(warning_text).contains("AI fixes requested")
    assert_that(warning_text).contains("AI lint is disabled")


def test_warn_ai_fix_disabled_no_warning_for_other_states():
    """Test that no warning is issued for non-qualifying state combinations."""
    logger = MagicMock()

    _warn_ai_fix_disabled(
        action=Action.FIX,
        ai_fix=True,
        ai_lint_enabled=False,
        logger=logger,
    )
    _warn_ai_fix_disabled(
        action=Action.CHECK,
        ai_fix=False,
        ai_lint_enabled=False,
        logger=logger,
    )
    _warn_ai_fix_disabled(
        action=Action.CHECK,
        ai_fix=True,
        ai_lint_enabled=True,
        logger=logger,
    )

    assert_that(logger.console_output.call_count).is_equal_to(0)


@pytest.mark.parametrize("output_format", ["json", "sarif", "JSON", "SARIF"])
def test_warn_ai_fix_disabled_suppressed_for_machine_formats(
    output_format: str,
) -> None:
    """Warning is suppressed for machine-readable output formats."""
    logger = MagicMock()

    _warn_ai_fix_disabled(
        action=Action.CHECK,
        ai_fix=True,
        ai_lint_enabled=False,
        logger=logger,
        output_format=output_format,
    )

    assert_that(logger.console_output.call_count).is_equal_to(0)


# ---------------------------------------------------------------------------
# ai_exit_code_override
# ---------------------------------------------------------------------------


def _config(**ai_kwargs: Any) -> LintroConfig:
    """Build a LintroConfig with the given AI settings.

    Args:
        **ai_kwargs: Keyword arguments for :class:`AIConfig`.

    Returns:
        A :class:`LintroConfig` carrying the requested AI settings.
    """
    return LintroConfig(ai=_ai_config(**ai_kwargs).model_dump())


def _ai_config(**ai_kwargs: Any) -> AIConfig:
    """Build an AIConfig with the given AI settings.

    Args:
        **ai_kwargs: Keyword arguments for :class:`AIConfig`.

    Returns:
        The requested AI configuration, pinned to the API transport.
    """
    return AIConfig(transport=AITransport.API, **ai_kwargs)


def test_ai_exit_code_override_is_false_without_result():
    """No AI result means no override."""
    override = ai_exit_code_override(
        ai_result=None,
        ai_config=_ai_config(enabled=True, fail_on_unfixed=True),
    )

    assert_that(override).is_false()


def test_ai_exit_code_override_true_for_unfixed_issues():
    """``fail_on_unfixed`` with remaining issues forces failure."""
    override = ai_exit_code_override(
        ai_result=AIResult(unfixed_issues=2),
        ai_config=_ai_config(enabled=True, fail_on_unfixed=True),
    )

    assert_that(override).is_true()


def test_ai_exit_code_override_false_when_unfixed_not_configured():
    """Unfixed issues alone do not force failure."""
    override = ai_exit_code_override(
        ai_result=AIResult(unfixed_issues=2),
        ai_config=_ai_config(enabled=True),
    )

    assert_that(override).is_false()


def test_ai_exit_code_override_true_for_ai_error():
    """``fail_on_ai_error`` with an AI error forces failure."""
    override = ai_exit_code_override(
        ai_result=AIResult(error=True),
        ai_config=_ai_config(enabled=True, fail_on_ai_error=True),
    )

    assert_that(override).is_true()


def test_ai_exit_code_override_false_for_ai_error_when_not_configured():
    """An AI error alone does not force failure."""
    override = ai_exit_code_override(
        ai_result=AIResult(error=True),
        ai_config=_ai_config(enabled=True),
    )

    assert_that(override).is_false()


# ---------------------------------------------------------------------------
# run_ai_layer
# ---------------------------------------------------------------------------


def _stub_hook(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: AIResult | None = None,
    error: Exception | None = None,
    should_run: bool = True,
) -> dict[str, Any]:
    """Install a fake ``AIPostExecutionHook`` and record its invocation.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        result: Result returned by ``execute``.
        error: Exception raised by ``execute`` instead of returning.
        should_run: Value returned by ``should_run``.

    Returns:
        A dict recording constructor and ``execute`` arguments.
    """
    recorded: dict[str, Any] = {}

    class _FakeHook:
        def __init__(
            self,
            lintro_config: LintroConfig,
            *,
            resolved_ai_config: ResolvedAIConfig | None = None,
            ai_fix: bool = False,
        ) -> None:
            recorded["resolved_ai_config"] = resolved_ai_config
            recorded["ai_config"] = (
                resolved_ai_config.config if resolved_ai_config is not None else None
            )
            recorded["ai_fix"] = ai_fix

        def should_run(self, action: Action) -> bool:
            recorded["should_run_action"] = action
            return should_run

        def execute(
            self,
            action: Action,
            all_results: list[ToolResult],
            *,
            console_logger: Any,
            output_format: str,
        ) -> AIResult:
            recorded["executed"] = True
            if error is not None:
                raise error
            return result or AIResult()

    import lintro.ai.hook as hook_module

    monkeypatch.setattr(hook_module, "AIPostExecutionHook", _FakeHook)
    return recorded


def test_run_ai_layer_returns_not_ran_when_gate_rejects_action(monkeypatch):
    """A closed ``should_run`` gate yields an outcome with ``ran`` False."""
    recorded = _stub_hook(monkeypatch, should_run=False)

    outcome = run_ai_layer(
        action=Action.TEST,
        all_results=[],
        lintro_config=_config(enabled=True),
        console_logger=MagicMock(),
        output_format="grid",
    )

    assert_that(outcome.ran).is_false()
    assert_that(outcome.force_failure).is_false()
    assert_that(recorded).does_not_contain_key("executed")


def test_run_ai_layer_runs_hook_and_reports_ran(monkeypatch):
    """A successful AI run reports ``ran`` with no forced failure."""
    _stub_hook(monkeypatch, result=AIResult(fixes_applied=1))

    outcome = run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=_config(enabled=True),
        console_logger=MagicMock(),
        output_format="grid",
    )

    assert_that(outcome.ran).is_true()
    assert_that(outcome.force_failure).is_false()


def test_run_ai_layer_forces_failure_for_unfixed_issues(monkeypatch):
    """``fail_on_unfixed`` propagates into the returned outcome."""
    _stub_hook(monkeypatch, result=AIResult(unfixed_issues=3))

    outcome = run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=_config(enabled=True, fail_on_unfixed=True),
        console_logger=MagicMock(),
        output_format="grid",
    )

    assert_that(outcome.ran).is_true()
    assert_that(outcome.force_failure).is_true()


def test_run_ai_layer_reraises_when_fail_on_ai_error(monkeypatch):
    """A provider failure propagates when ``fail_on_ai_error`` is set."""
    _stub_hook(monkeypatch, error=RuntimeError("provider down"))

    assert_that(run_ai_layer).raises(RuntimeError).when_called_with(
        action=Action.CHECK,
        all_results=[],
        lintro_config=_config(enabled=True, fail_on_ai_error=True),
        console_logger=MagicMock(),
        output_format="grid",
    )


def test_run_ai_layer_swallows_error_and_warns_without_fail_on_ai_error(monkeypatch):
    """Without ``fail_on_ai_error`` the failure warns and does not force exit."""
    _stub_hook(monkeypatch, error=RuntimeError("provider down"))
    console_logger = MagicMock()

    outcome = run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=_config(enabled=True),
        console_logger=console_logger,
        output_format="grid",
    )

    assert_that(outcome.ran).is_true()
    assert_that(outcome.force_failure).is_false()
    assert_that(console_logger.console_output.call_count).is_equal_to(1)
    assert_that(console_logger.console_output.call_args[0][0]).contains(
        "AI enhancement failed",
    )


def test_run_ai_layer_error_warning_suppressed_for_json(monkeypatch):
    """The failure warning is suppressed for machine-readable formats."""
    _stub_hook(monkeypatch, error=RuntimeError("provider down"))
    console_logger = MagicMock()

    outcome = run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=_config(enabled=True),
        console_logger=console_logger,
        output_format="json",
    )

    assert_that(outcome.ran).is_true()
    assert_that(console_logger.console_output.call_count).is_equal_to(0)


def test_run_ai_layer_error_forces_failure_is_unreachable_when_reraising(monkeypatch):
    """A swallowed error still honors ``fail_on_unfixed`` accounting."""
    _stub_hook(monkeypatch, error=RuntimeError("provider down"))

    outcome = run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=_config(enabled=True, fail_on_unfixed=True),
        console_logger=MagicMock(),
        output_format="grid",
    )

    # The synthesized AIResult reports zero unfixed issues, so no override.
    assert_that(outcome.force_failure).is_false()


def test_run_ai_layer_resolves_effective_ai_fix_from_config(monkeypatch):
    """``ai.default_fix`` turns on AI fixes without the CLI flag."""
    recorded = _stub_hook(monkeypatch)

    run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=_config(enabled=True, default_fix=True),
        console_logger=MagicMock(),
        output_format="grid",
        ai_fix=False,
        transport="cli",
    )

    assert_that(recorded.get("ai_fix")).is_true()
    # #2299: ``--transport`` on the lint path is an ordinary CLI overlay on
    # the one resolver, so the hook receives it already resolved, with
    # provenance, instead of as a separate post-resolution argument.
    resolved = recorded["resolved_ai_config"]
    assert_that(resolved.config.transport).is_equal_to(AITransport.CLI)
    assert_that(resolved.source_of("transport")).is_equal_to(ConfigSource.FLAG)


def test_run_ai_layer_warns_when_ai_fix_requested_but_lint_disabled(monkeypatch):
    """The disabled-AI warning survives the move into the facade."""
    _stub_hook(monkeypatch, should_run=False)
    console_logger = MagicMock()

    outcome = run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=_config(enabled=False),
        console_logger=console_logger,
        output_format="grid",
        ai_fix=True,
    )

    assert_that(outcome.ran).is_false()
    assert_that(console_logger.console_output.call_count).is_equal_to(1)
    assert_that(console_logger.console_output.call_args[0][0]).contains(
        "AI fixes requested",
    )


# ---------------------------------------------------------------------------
# render_ai_status
# ---------------------------------------------------------------------------


def test_render_ai_status_delegates_to_display_module():
    """The facade returns exactly what the display module renders."""
    from lintro.ai.display.status import render_ai_status as display_render

    ai_config = AIConfig(enabled=False, transport=AITransport.API)

    assert_that(render_ai_status(ai_config=ai_config, is_ci=False)).is_equal_to(
        display_render(ai_config=ai_config, is_ci=False),
    )


def test_interface_public_surface_stays_small():
    """The facade exposes exactly five public names.

    Collapsing the executor's three AI seams (issue #1823) made
    ``run_ai_layer`` an implementation detail of
    :func:`~lintro.ai.interface.enhance_artifact`; #2299 added
    ``resolve_effective_ai_config``, the provenance-carrying resolver every
    AI surface shares, alongside the value-only ``resolve_ai_config``.
    """
    assert_that(sorted(interface.__all__)).is_equal_to(
        [
            "enhance_artifact",
            "render_ai_status",
            "resolve_ai_config",
            "resolve_effective_ai_config",
            "sarif_enrichment_from_results",
        ],
    )
