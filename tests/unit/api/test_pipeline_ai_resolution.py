"""The run pipeline resolves ``ai:`` once, with the invocation's flags (#2374).

The pre-execution AI status row used to resolve the raw ``ai:`` mapping
without the invocation's CLI flags, while the AI layer resolved it again with
them. ``lintro check --transport cli`` therefore printed ``transport: api
(config)`` and then ran on ``cli``. The pipeline now resolves once per run and
shares the result with every AI seam.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli

#: AI on, transport pinned to ``api`` in the project, AI lint off so the hook
#: never contacts a provider.
_PROJECT_CONFIG = (
    "tools:\n"
    "  ruff:\n"
    "    enabled: true\n"
    "ai:\n"
    "  enabled: true\n"
    "  lint: false\n"
    "  provider: anthropic\n"
    "  transport: api\n"
)


@pytest.fixture
def project(ai_config_project: Callable[[str], Path]) -> Path:
    """Write an AI-enabled one-file project and make it the working directory.

    Args:
        ai_config_project: Writer for an isolated project config.

    Returns:
        The project root.
    """
    root = ai_config_project(_PROJECT_CONFIG)
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _check(*extra: str) -> Any:
    """Run ``lintro check`` on the project's one file with human output.

    Args:
        *extra: Additional CLI arguments.

    Returns:
        The click ``Result``.
    """
    return CliRunner().invoke(
        cli,
        ["check", *extra, "--tools", "ruff", "--no-art", "a.py"],
    )


@pytest.mark.usefixtures("project")
def test_check_transport_flag_reaches_the_pre_execution_summary() -> None:
    """``check --transport cli`` shows the flag's value and provenance."""
    result = _check("--transport", "cli")

    assert_that(result.exit_code).described_as(result.output).is_equal_to(0)
    assert_that(result.output).contains("transport: cli (flag)")
    assert_that(result.output).does_not_contain("transport: api (config)")


@pytest.mark.usefixtures("project")
def test_check_without_transport_flag_reports_the_project_value() -> None:
    """Control: with no flag the summary still reports the project layer."""
    result = _check()

    assert_that(result.exit_code).described_as(result.output).is_equal_to(0)
    assert_that(result.output).contains("transport: api (config)")


@pytest.mark.usefixtures("project")
def test_ai_section_is_resolved_once_per_lint_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The status row and the AI layer share a single resolution.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import lintro.ai.effective_config as effective_config

    real_resolver = effective_config.resolve_effective_ai_config
    transports_seen: list[str | None] = []

    def _recording_resolver(
        mapping: Mapping[str, Any] | None,
        **kwargs: Any,
    ) -> Any:
        """Record each resolution's transport flag, then resolve for real.

        Args:
            mapping: Raw ``ai:`` mapping.
            **kwargs: Resolver keyword arguments.

        Returns:
            The real resolver's result.
        """
        overrides = kwargs.get("cli_overrides")
        transports_seen.append(getattr(overrides, "transport", None))
        return real_resolver(mapping, **kwargs)

    monkeypatch.setattr(
        effective_config,
        "resolve_effective_ai_config",
        _recording_resolver,
    )

    result = _check("--transport", "cli")

    assert_that(result.exit_code).described_as(result.output).is_equal_to(0)
    assert_that(result.output).contains("transport: cli (flag)")
    assert_that(transports_seen).is_equal_to(["cli"])


def test_early_exit_run_reports_an_unknown_ai_key_once(
    ai_config_project: Callable[[str], Path],
) -> None:
    """A run that stops before any tool still names a typo'd ``ai.*`` key.

    The status row is the first seam to resolve ``ai:`` now, and it does so
    before the run knows it will exit early. With diagnostics tied to
    ``ai_enabled``, the unknown-key warning is reported there, once, rather
    than only by the post-run AI layer that an early exit never reaches.
    This is a deliberate change from the pre-#2374 behaviour, which was
    silent here.

    Args:
        ai_config_project: Writer for an isolated project config.
    """
    root = ai_config_project("ai:\n  enabled: false\n  bogus_key: 1\n")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["check", "a.py", "--tools", "nosuchtool", "--no-art"],
    )

    assert_that(result.exit_code).described_as(result.output).is_equal_to(1)
    assert_that(result.output).contains("nosuchtool")
    warning = "Unknown AI config keys ignored: bogus_key"
    assert_that(result.output.count(warning)).is_equal_to(1)
