"""``lintro doctor`` for Cursor's ``transport: api`` (#3253).

Doctor must report the SDK with the ``lintro[cursor]`` hint (never
``lintro[ai]``), report ``CURSOR_API_KEY`` without offering
``ai.api_base_url`` as a substitute, and flag a configured base URL, because
the Cursor SDK has no endpoint override.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.doctor_checks import AICheckResult, check_ai_configuration
from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.enums.tool_status import ToolStatus

_KEY_ENV = "CURSOR_API_KEY"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the key and transcript variables so results are deterministic.

    Args:
        monkeypatch: Pytest environment patcher.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.delenv("LINTRO_AI_TRANSCRIPT", raising=False)


def _sdk_installed(monkeypatch: pytest.MonkeyPatch, *, installed: bool) -> None:
    """Pretend the Cursor SDK is, or is not, importable.

    Args:
        monkeypatch: Pytest attribute patcher.
        installed: Whether the SDK should be reported present.
    """
    monkeypatch.setattr(
        "lintro.ai.doctor_checks.is_provider_available",
        lambda provider, *, transport: installed,
    )


def _cursor_api(*, api_base_url: str | None = None) -> AIConfig:
    """Return an enabled Cursor ``transport: api`` config.

    Args:
        api_base_url: Optional ``ai.api_base_url``.

    Returns:
        The config.
    """
    return AIConfig(
        enabled=True,
        review=True,
        provider=AIProvider.CURSOR,
        transport=AITransport.API,
        api_base_url=api_base_url,
    )


def _by_name(results: list[AICheckResult]) -> dict[str, AICheckResult]:
    """Index doctor results by check name.

    Args:
        results: Doctor results.

    Returns:
        The results keyed by name.
    """
    return {result.name: result for result in results}


def test_missing_sdk_points_at_the_cursor_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``cursor-sdk`` doctor says to install ``lintro[cursor]``.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    _sdk_installed(monkeypatch, installed=False)

    results = check_ai_configuration(_cursor_api())

    assert_that([r.name for r in results]).is_equal_to(["ai.api.sdk.cursor"])
    assert_that(results[0].status).is_equal_to(ToolStatus.MISSING)
    assert_that(results[0].hint).is_equal_to(
        "Install with: uv pip install 'lintro[cursor]'",
    )


def test_the_pairing_is_no_longer_incompatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``provider: cursor`` + ``transport: api`` is a supported pairing now.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    _sdk_installed(monkeypatch, installed=True)
    monkeypatch.setenv(_KEY_ENV, "k")

    results = _by_name(check_ai_configuration(_cursor_api()))

    assert_that(results).does_not_contain_key("ai.provider+transport")
    assert_that(results[f"ai.api.{_KEY_ENV}"].status).is_equal_to(ToolStatus.OK)
    assert_that(results[f"ai.api.{_KEY_ENV}"].message).is_equal_to(
        f"API credentials configured via {_KEY_ENV}",
    )


def test_missing_key_hint_does_not_offer_a_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor has no endpoint override, so the hint names the key alone.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    _sdk_installed(monkeypatch, installed=True)

    result = _by_name(check_ai_configuration(_cursor_api()))[f"ai.api.{_KEY_ENV}"]

    assert_that(result.status).is_equal_to(ToolStatus.MISSING)
    assert_that(result.hint).is_equal_to(f"Export {_KEY_ENV}")


def test_a_base_url_is_flagged_and_does_not_stand_in_for_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ai.api_base_url`` is incompatible and never satisfies the key check.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    _sdk_installed(monkeypatch, installed=True)

    results = _by_name(
        check_ai_configuration(_cursor_api(api_base_url="https://gw.example")),
    )

    assert_that(results["ai.api_base_url"].status).is_equal_to(
        ToolStatus.INCOMPATIBLE,
    )
    assert_that(results["ai.api_base_url"].message).is_equal_to(
        "cursor API transport does not accept ai.api_base_url",
    )
    assert_that(results[f"ai.api.{_KEY_ENV}"].status).is_equal_to(
        ToolStatus.MISSING,
    )


def test_other_providers_keep_the_base_url_wording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic still counts ``ai.api_base_url`` as a credential substitute.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    _sdk_installed(monkeypatch, installed=True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    results = check_ai_configuration(
        AIConfig(
            enabled=True,
            review=True,
            provider=AIProvider.ANTHROPIC,
            transport=AITransport.API,
            api_base_url="https://gw.example",
        ),
    )

    assert_that([r.name for r in results]).is_equal_to(["ai.api.ANTHROPIC_API_KEY"])
    assert_that(results[0].message).is_equal_to(
        "API credentials configured via ANTHROPIC_API_KEY or api_base_url",
    )
