"""Run provenance: endpoint, credential, reported models and verification (#2867).

ADR-0011 decision B says a run record reports where its requests went and
what answered, not what was configured. These tests pin the detection rules,
the fixed order the verification states are assigned in, the ledger seam
through ``call_ai``, and how a finished run's cost figure is read.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport, CostBasis, CredentialKind, ModelVerification
from lintro.ai.invoke import call_ai
from lintro.ai.provenance import (
    ANTHROPIC_AUTH_TOKEN_ENV,
    ANTHROPIC_BASE_URL_ENV,
    CLAUDE_OAUTH_TOKEN_ENV,
    CODEX_SESSION_FILE,
    CURSOR_API_KEY_ENV,
    REVIEW_ALLOWED_MODELS_ENV,
    REVIEW_ENDPOINT_ENV,
    REVIEW_FIRST_PARTY_ENV,
    REVIEW_LANE_ENV,
    ModelLedger,
    auth_mode_for,
    current_model_ledger,
    detect_run_provenance,
    host_of,
    model_ledger,
    recorded_cost_basis,
    verify_models,
)
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.anthropic.metadata import ANTHROPIC_MANAGED_SETTINGS_ENV
from lintro.ai.providers.reported_model import ReportedModel, parse_model_usage
from lintro.ai.providers.response import AIResponse

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "ai"
    / "cli_replay"
    / "claude"
    / "2.1.286.jsonl"
)


def _reported(*names: str) -> tuple[ReportedModel, ...]:
    return tuple(ReportedModel(model=name, canonical=name) for name in names)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """Build an environment whose Claude settings lookups stay inside *tmp_path*.

    Args:
        tmp_path: Per-test directory; its ``claude-config`` child becomes the
            user config dir and the managed-settings override points inside it.
        **extra: Variables the test sets.

    Returns:
        The environment mapping to hand to :func:`detect_run_provenance`.
    """
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir(exist_ok=True)
    return {
        "CLAUDE_CONFIG_DIR": str(config_dir),
        ANTHROPIC_MANAGED_SETTINGS_ENV: str(tmp_path / "managed-settings.json"),
        **extra,
    }


# --- host_of -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://api.z.ai/api/anthropic", "api.z.ai"),
        ("https://API.Anthropic.com:443/v1?x=1", "api.anthropic.com"),
        ("api.openai.com", "api.openai.com"),
        ("   ", ""),
        ("", ""),
    ],
)
def test_host_of_keeps_only_the_lowercase_host(url: str, host: str) -> None:
    """The record carries a host, never a path, port or query."""
    assert_that(host_of(url)).is_equal_to(host)


# --- detect_run_provenance ---------------------------------------------------


def test_unset_override_is_the_providers_own_endpoint(tmp_path: Path) -> None:
    """No base-URL override means the first-party host, with the OAuth session."""
    provenance = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        env=_env(tmp_path, **{CLAUDE_OAUTH_TOKEN_ENV: "tok"}),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.endpoint).is_equal_to("api.anthropic.com")
    assert_that(provenance.first_party).is_true()
    assert_that(provenance.credential).is_equal_to(CredentialKind.CLAUDE_OAUTH)
    assert_that(provenance.lane).is_empty()


def test_a_gateway_override_is_not_first_party(tmp_path: Path) -> None:
    """The incident shape: a Z.ai base URL with a gateway token (ADR-0011)."""
    provenance = detect_run_provenance(
        provider="anthropic",
        transport=AITransport.CLI,
        env=_env(
            tmp_path,
            **{
                ANTHROPIC_BASE_URL_ENV: "https://api.z.ai/api/anthropic",
                ANTHROPIC_AUTH_TOKEN_ENV: "zai-token",
            },
        ),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.endpoint).is_equal_to("api.z.ai")
    assert_that(provenance.first_party).is_false()
    assert_that(provenance.credential).is_equal_to(CredentialKind.ZAI_TOKEN)


def test_an_override_naming_the_canonical_host_stays_first_party(
    tmp_path: Path,
) -> None:
    """Spelling the provider's own host out is not a lane switch."""
    provenance = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        env=_env(tmp_path, **{ANTHROPIC_BASE_URL_ENV: "https://api.anthropic.com"}),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.first_party).is_true()


def test_the_lane_row_exports_win_over_the_host_comparison(tmp_path: Path) -> None:
    """In CI the manifest row, not a host comparison, decides (ADR-0011 A)."""
    provenance = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        env=_env(
            tmp_path,
            **{
                ANTHROPIC_BASE_URL_ENV: "https://api.anthropic.com",
                REVIEW_LANE_ENV: "zai-glm-gateway",
                REVIEW_ENDPOINT_ENV: "https://api.z.ai/api/anthropic",
                REVIEW_FIRST_PARTY_ENV: "false",
                REVIEW_ALLOWED_MODELS_ENV: "glm-5.3-flash, glm-5",
            },
        ),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.lane).is_equal_to("zai-glm-gateway")
    assert_that(provenance.endpoint).is_equal_to("api.z.ai")
    assert_that(provenance.first_party).is_false()
    assert_that(provenance.allowed_models).is_equal_to(("glm-5.3-flash", "glm-5"))


def test_a_claude_settings_env_block_routes_like_the_variable(tmp_path: Path) -> None:
    """A base URL in a settings file's ``env`` block is read as an override."""
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {"env": {ANTHROPIC_BASE_URL_ENV: "https://proxy.example/anthropic"}},
        ),
        encoding="utf-8",
    )

    provenance = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        env=_env(tmp_path),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.endpoint).is_equal_to("proxy.example")
    assert_that(provenance.first_party).is_false()


def test_bare_billing_is_an_api_key_whatever_session_is_present(
    tmp_path: Path,
) -> None:
    """Under ``--bare`` the call bills the key, so the credential is the key."""
    provenance = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        bare=True,
        env=_env(
            tmp_path,
            **{CLAUDE_OAUTH_TOKEN_ENV: "tok", "ANTHROPIC_API_KEY": "sk-ant"},
        ),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.credential).is_equal_to(CredentialKind.API_KEY)


def test_the_codex_session_file_is_the_openai_cli_credential(tmp_path: Path) -> None:
    """The Codex credential is a file on disk, not an environment variable."""
    session = tmp_path / CODEX_SESSION_FILE
    session.parent.mkdir()
    session.write_text("{}", encoding="utf-8")

    provenance = detect_run_provenance(
        provider=AIProvider.OPENAI,
        transport=AITransport.CLI,
        env=_env(tmp_path),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.credential).is_equal_to(CredentialKind.CODEX_OAUTH)
    assert_that(provenance.endpoint).is_equal_to("api.openai.com")


@pytest.mark.parametrize(
    ("provider", "env", "credential"),
    [
        (AIProvider.CURSOR, {CURSOR_API_KEY_ENV: "key"}, CredentialKind.CURSOR_API_KEY),
        (AIProvider.CURSOR, {}, CredentialKind.UNKNOWN),
        (AIProvider.OPENAI, {"OPENAI_API_KEY": "sk"}, CredentialKind.API_KEY),
        (AIProvider.ANTHROPIC, {}, CredentialKind.UNKNOWN),
    ],
)
def test_each_cli_credential_is_named_by_its_binding(
    tmp_path: Path,
    provider: AIProvider,
    env: dict[str, str],
    credential: CredentialKind,
) -> None:
    """The credential kinds are the manifest's binding names."""
    provenance = detect_run_provenance(
        provider=provider,
        transport=AITransport.CLI,
        env=_env(tmp_path, **env),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.credential).is_equal_to(credential)


def test_the_api_transport_is_always_an_api_key(tmp_path: Path) -> None:
    """An SDK call has no session to bill; the key is the credential."""
    provenance = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
        env=_env(tmp_path, **{CLAUDE_OAUTH_TOKEN_ENV: "tok"}),
        home=tmp_path,
    )

    assert_that(provenance.credential).is_equal_to(CredentialKind.API_KEY)


def test_an_unknown_provider_records_nothing_it_cannot_know(tmp_path: Path) -> None:
    """A provider name outside the registry yields no endpoint and no credential."""
    provenance = detect_run_provenance(
        provider="mystery",
        transport=AITransport.CLI,
        env=_env(tmp_path),
        home=tmp_path,
    )

    assert_that(provenance.endpoint).is_empty()
    assert_that(provenance.credential).is_equal_to(CredentialKind.UNKNOWN)


# --- auth_mode_for -----------------------------------------------------------


@pytest.mark.parametrize(
    ("credential", "auth_mode"),
    [
        (CredentialKind.CLAUDE_OAUTH, "subscription"),
        (CredentialKind.CODEX_OAUTH, "subscription"),
        (CredentialKind.API_KEY, "api_key"),
        (CredentialKind.CURSOR_API_KEY, "api_key"),
        (CredentialKind.ZAI_TOKEN, "gateway_token"),
        (CredentialKind.UNKNOWN, ""),
        ("not-a-kind", ""),
    ],
)
def test_auth_mode_follows_the_credential(
    credential: CredentialKind | str,
    auth_mode: str,
) -> None:
    """A gateway token never reads as a subscription (ADR-0011, Context)."""
    assert_that(auth_mode_for(credential)).is_equal_to(auth_mode)


# --- parse_model_usage -------------------------------------------------------


def test_the_pinned_envelope_names_the_model_that_answered() -> None:
    """The replay fixture's ``modelUsage`` parses with canonical and provider."""
    envelope = json.loads(_FIXTURE.read_text(encoding="utf-8").splitlines()[0])

    reported = parse_model_usage(envelope["modelUsage"])

    assert_that(reported).is_length(1)
    assert_that(reported[0].model).is_equal_to("claude-sonnet-4-6")
    assert_that(reported[0].canonical).is_equal_to("claude-sonnet-4-6")
    assert_that(reported[0].provider).is_equal_to("firstParty")


@pytest.mark.parametrize("raw", [None, [], "claude", 3, {"": {}}, {3: {}}])
def test_a_missing_or_malformed_block_reports_no_models(raw: object) -> None:
    """Anything that is not a keyed mapping reads as "no usage reported"."""
    assert_that(parse_model_usage(raw)).is_empty()


def test_an_alias_key_keeps_its_canonical_name() -> None:
    """The key is what the CLI called it; ``canonicalModel`` is what it was."""
    reported = parse_model_usage(
        {"sonnet": {"canonicalModel": "claude-sonnet-4-6"}, "haiku": "oops"},
    )

    assert_that([model.name for model in reported]).is_equal_to(
        ["claude-sonnet-4-6", "haiku"],
    )


# --- verify_models -----------------------------------------------------------


def test_no_completed_call_has_no_state_of_its_own() -> None:
    """A round that made no call leaves the verdict to the rounds it reuses."""
    assert_that(
        verify_models(requested="claude-sonnet-4-6", calls=[], first_party=True),
    ).is_none()


def test_a_non_first_party_endpoint_is_unverifiable_whatever_it_reported() -> None:
    """Off the provider's host the envelope is not evidence; precedence holds."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[_reported("claude-opus-5-5"), ()],
        first_party=False,
    )

    assert_that(state).is_equal_to(ModelVerification.UNVERIFIABLE)


def test_every_call_reporting_the_requested_model_is_verified() -> None:
    """Aliases normalise to the canonical name before comparison."""
    state = verify_models(
        requested="claude-sonnet-4-6",
        calls=[
            (ReportedModel(model="sonnet", canonical="claude-sonnet-4-6"),),
            _reported("CLAUDE-SONNET-4-6"),
        ],
        first_party=True,
    )

    assert_that(state).is_equal_to(ModelVerification.VERIFIED)


def test_a_call_that_answered_with_another_model_is_a_mismatch() -> None:
    """One call off the requested model fails the round."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[_reported("claude-opus-5-5"), _reported("glm-5.3-flash")],
        first_party=True,
    )

    assert_that(state).is_equal_to(ModelVerification.MISMATCH)


def test_a_helper_model_outside_the_allowed_list_is_a_mismatch() -> None:
    """Every reported model must be one the lane allows."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[_reported("claude-opus-5-5", "claude-haiku-4-5")],
        first_party=True,
    )

    assert_that(state).is_equal_to(ModelVerification.MISMATCH)


def test_an_allowed_helper_model_keeps_the_round_verified() -> None:
    """The lane row's allowed models widen what a call may report."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[_reported("claude-opus-5-5", "claude-haiku-4-5")],
        first_party=True,
        allowed=("claude-haiku-4-5",),
    )

    assert_that(state).is_equal_to(ModelVerification.VERIFIED)


def test_mismatch_outranks_unreported_which_outranks_verified() -> None:
    """The states are assigned in the ADR's fixed order over every call."""
    unreported = verify_models(
        requested="claude-opus-5-5",
        calls=[_reported("claude-opus-5-5"), ()],
        first_party=True,
    )
    mismatch = verify_models(
        requested="claude-opus-5-5",
        calls=[(), _reported("glm-5.3-flash")],
        first_party=True,
    )

    assert_that(unreported).is_equal_to(ModelVerification.UNREPORTED)
    assert_that(mismatch).is_equal_to(ModelVerification.MISMATCH)


# --- ledger ------------------------------------------------------------------


def test_the_ledger_collects_models_in_first_seen_order_without_repeats() -> None:
    """``reported_models`` is a set in order; ``cost_reported`` is sticky."""
    ledger = ModelLedger()
    ledger.record(
        AIResponse(content="", model="m", reported_models=_reported("a", "b")),
    )
    ledger.record(
        AIResponse(
            content="",
            model="m",
            reported_models=_reported("b", "c"),
            cost_reported=True,
        ),
    )
    ledger.record(AIResponse(content="", model="m"))

    assert_that(ledger.reported_models).is_equal_to(("a", "b", "c"))
    assert_that(ledger.calls).is_length(3)
    assert_that(ledger.calls[2]).is_empty()
    assert_that(ledger.cost_reported).is_true()


def test_no_ledger_is_open_outside_the_scope() -> None:
    """The context manager owns the ledger's lifetime."""
    assert_that(current_model_ledger()).is_none()
    with model_ledger() as ledger:
        assert_that(current_model_ledger()).is_same_as(ledger)
    assert_that(current_model_ledger()).is_none()


async def test_call_ai_reports_each_completed_call_to_the_open_ledger() -> None:
    """The one seam every provider call crosses feeds the ledger."""
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=AIResponse(
            content="ok",
            model="claude-sonnet-4-6",
            reported_models=_reported("claude-sonnet-4-6"),
            cost_reported=True,
        ),
    )
    config = AIConfig(enabled=True, transport=AITransport.API, max_retries=0)

    with model_ledger() as ledger:
        await call_ai(
            provider=provider,
            ai_config=config,
            user_prompt="hello",
            system_prompt=None,
            budget=None,
        )
        await call_ai(
            provider=provider,
            ai_config=config,
            user_prompt="again",
            system_prompt=None,
            budget=None,
        )

    assert_that(ledger.calls).is_length(2)
    assert_that(ledger.reported_models).is_equal_to(("claude-sonnet-4-6",))
    assert_that(ledger.cost_reported).is_true()


# --- recorded_cost_basis -----------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "first_party", "cost_reported", "expected"),
    [
        (CostBasis.BILLED, True, False, CostBasis.BILLED),
        (CostBasis.ESTIMATED, False, True, CostBasis.ESTIMATED),
        (CostBasis.UNPRICEABLE, True, True, CostBasis.LIST_PRICE_SHADOW),
        (CostBasis.UNPRICEABLE, True, False, CostBasis.UNPRICEABLE),
        (CostBasis.UNPRICEABLE, False, True, CostBasis.UNKNOWN),
        (CostBasis.UNKNOWN, False, False, CostBasis.UNKNOWN),
        (CostBasis.LIST_PRICE_SHADOW, True, False, CostBasis.UNPRICEABLE),
    ],
)
def test_the_recorded_basis_follows_the_endpoint_and_the_envelope(
    configured: CostBasis,
    first_party: bool,
    cost_reported: bool,
    expected: CostBasis,
) -> None:
    """A subscription figure is a shadow on the provider's host, unknown off it."""
    basis = recorded_cost_basis(
        configured=configured,
        first_party=first_party,
        cost_reported=cost_reported,
    )

    assert_that(basis).is_equal_to(expected)
