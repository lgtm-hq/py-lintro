"""Run provenance: endpoint, credential, reported models and verification (#2867).

ADR-0011 decision B says a run record reports where its requests went and
what answered, not what was configured. These tests pin the detection rules,
the fixed order the verification states are assigned in, the ledger seam
through ``call_ai``, how a finished run's cost figure is read, and what the
transport profile resolves before the run.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
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
    REVIEW_REPORTS_MODEL_USAGE_ENV,
    REVIEW_SHADOW_PRICE_MODEL_ENV,
    LedgerCall,
    ModelLedger,
    RunProvenance,
    auth_mode_for,
    current_model_ledger,
    detect_run_provenance,
    host_of,
    model_ledger,
    recorded_cost_basis,
    verify_models,
)
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers._api_common import finish_api_completion
from lintro.ai.providers.anthropic.metadata import ANTHROPIC_MANAGED_SETTINGS_ENV
from lintro.ai.providers.reported_model import ReportedModel, parse_model_usage
from lintro.ai.providers.response import AIResponse
from lintro.ai.transport import resolve_transport_settings

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


def _call(requested: str, *reported: str) -> LedgerCall:
    return LedgerCall(requested=requested, reported=_reported(*reported))


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


def _detect(tmp_path: Path, **extra: str) -> RunProvenance:
    return detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        env=_env(tmp_path, **extra),
        home=tmp_path,
        cwd=str(tmp_path),
    )


# --- host_of -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://api.z.ai/api/anthropic", "api.z.ai"),
        ("https://API.Anthropic.com:443/v1?x=1", "api.anthropic.com"),
        ("https://proxy.example:8443/v1", "proxy.example"),
        ("api.openai.com", "api.openai.com"),
        ("   ", ""),
        ("", ""),
    ],
)
def test_host_of_keeps_only_the_lowercase_host(url: str, host: str) -> None:
    """The record carries a host, never a path, port or query."""
    assert_that(host_of(url)).is_equal_to(host)


# --- endpoint and first-party -------------------------------------------------


def test_unset_override_is_the_providers_own_endpoint(tmp_path: Path) -> None:
    """No base-URL override means the first-party host, with the OAuth session."""
    provenance = _detect(tmp_path, **{CLAUDE_OAUTH_TOKEN_ENV: "tok"})

    assert_that(provenance.endpoint).is_equal_to("api.anthropic.com")
    assert_that(provenance.first_party).is_true()
    assert_that(provenance.credential).is_equal_to(CredentialKind.CLAUDE_OAUTH)
    assert_that(provenance.lane).is_empty()
    assert_that(provenance.reports_model_usage).is_true()


def test_a_gateway_override_is_not_first_party(tmp_path: Path) -> None:
    """The incident shape: a Z.ai base URL with a gateway token (ADR-0011)."""
    provenance = _detect(
        tmp_path,
        **{
            ANTHROPIC_BASE_URL_ENV: "https://api.z.ai/api/anthropic",
            ANTHROPIC_AUTH_TOKEN_ENV: "zai-token",
        },
    )

    assert_that(provenance.endpoint).is_equal_to("api.z.ai")
    assert_that(provenance.first_party).is_false()
    assert_that(provenance.credential).is_equal_to(CredentialKind.ZAI_TOKEN)


def test_an_override_naming_the_canonical_host_stays_first_party(
    tmp_path: Path,
) -> None:
    """Spelling the provider's own host out is not a lane switch."""
    provenance = _detect(
        tmp_path,
        **{ANTHROPIC_BASE_URL_ENV: "https://api.anthropic.com"},
    )

    assert_that(provenance.first_party).is_true()


def test_the_lane_row_decides_first_party_and_what_is_allowed(tmp_path: Path) -> None:
    """In CI the manifest row, not a host comparison, decides (ADR-0011 A)."""
    provenance = _detect(
        tmp_path,
        **{
            REVIEW_LANE_ENV: "zai-glm-gateway",
            REVIEW_ENDPOINT_ENV: "https://api.z.ai/api/anthropic",
            REVIEW_FIRST_PARTY_ENV: "false",
            REVIEW_ALLOWED_MODELS_ENV: "glm-5.3-flash, glm-5",
            REVIEW_REPORTS_MODEL_USAGE_ENV: "false",
            REVIEW_SHADOW_PRICE_MODEL_ENV: "claude-sonnet-4-6",
        },
    )

    assert_that(provenance.lane).is_equal_to("zai-glm-gateway")
    assert_that(provenance.endpoint).is_equal_to("api.z.ai")
    assert_that(provenance.first_party).is_false()
    assert_that(provenance.allowed_models).is_equal_to(("glm-5.3-flash", "glm-5"))
    assert_that(provenance.reports_model_usage).is_false()
    assert_that(provenance.shadow_price_model).is_equal_to("claude-sonnet-4-6")


def test_the_actual_override_is_recorded_over_the_declared_endpoint(
    tmp_path: Path,
) -> None:
    """A lane row declaring Anthropic cannot hide a Z.ai override (ADR-0011 B)."""
    provenance = _detect(
        tmp_path,
        **{
            REVIEW_LANE_ENV: "claude-subscription",
            REVIEW_ENDPOINT_ENV: "https://api.anthropic.com",
            REVIEW_FIRST_PARTY_ENV: "true",
            ANTHROPIC_BASE_URL_ENV: "https://api.z.ai/api/anthropic",
        },
    )

    assert_that(provenance.endpoint).is_equal_to("api.z.ai")


def test_a_lane_row_that_does_not_say_is_not_first_party(tmp_path: Path) -> None:
    """A lane without ``LINTRO_REVIEW_FIRST_PARTY`` fails closed."""
    provenance = _detect(tmp_path, **{REVIEW_LANE_ENV: "claude-subscription"})

    assert_that(provenance.first_party).is_false()


def test_a_claude_settings_env_block_routes_like_the_variable(tmp_path: Path) -> None:
    """A base URL or bearer token in a settings ``env`` block is read as set."""
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "env": {
                    ANTHROPIC_BASE_URL_ENV: "https://proxy.example/anthropic",
                    ANTHROPIC_AUTH_TOKEN_ENV: "bearer",
                },
            },
        ),
        encoding="utf-8",
    )

    provenance = _detect(tmp_path)

    assert_that(provenance.endpoint).is_equal_to("proxy.example")
    assert_that(provenance.first_party).is_false()
    assert_that(provenance.credential).is_equal_to(CredentialKind.ZAI_TOKEN)


# --- credential precedence ----------------------------------------------------


def test_the_bearer_token_outranks_the_oauth_token(tmp_path: Path) -> None:
    """Claude Code's documented precedence: bearer, then key, then OAuth."""
    provenance = _detect(
        tmp_path,
        **{CLAUDE_OAUTH_TOKEN_ENV: "tok", ANTHROPIC_AUTH_TOKEN_ENV: "bearer"},
    )

    assert_that(provenance.credential).is_equal_to(CredentialKind.ZAI_TOKEN)


def test_the_api_key_outranks_the_oauth_token(tmp_path: Path) -> None:
    """An API key present beside the OAuth token is what the CLI sends."""
    provenance = _detect(
        tmp_path,
        **{CLAUDE_OAUTH_TOKEN_ENV: "tok", "ANTHROPIC_API_KEY": "sk-ant"},
    )

    assert_that(provenance.credential).is_equal_to(CredentialKind.API_KEY)


def test_bare_mode_never_reads_the_oauth_token(tmp_path: Path) -> None:
    """Under ``--bare`` an OAuth token alone is no credential at all."""
    provenance = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        bare=True,
        env=_env(tmp_path, **{CLAUDE_OAUTH_TOKEN_ENV: "tok"}),
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(provenance.credential).is_equal_to(CredentialKind.UNKNOWN)


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
    assert_that(provenance.reports_model_usage).is_false()


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


def test_the_api_transport_reads_its_endpoint_from_the_config(tmp_path: Path) -> None:
    """``ai.api_base_url`` pointed at a gateway makes the key a gateway token."""
    first_party = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
        env=_env(tmp_path, **{CLAUDE_OAUTH_TOKEN_ENV: "tok"}),
        home=tmp_path,
    )
    gateway = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
        env=_env(tmp_path),
        home=tmp_path,
        configured_base_url="https://api.z.ai/api/anthropic",
    )

    assert_that(first_party.credential).is_equal_to(CredentialKind.API_KEY)
    assert_that(first_party.endpoint).is_equal_to("api.anthropic.com")
    assert_that(gateway.credential).is_equal_to(CredentialKind.ZAI_TOKEN)
    assert_that(gateway.endpoint).is_equal_to("api.z.ai")
    assert_that(gateway.first_party).is_false()


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


# --- parse_model_usage and the API path ----------------------------------------


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


def test_the_api_path_reports_the_served_model_not_the_request() -> None:
    """Evidence is what the vendor says it served; no answer is no evidence."""
    served = finish_api_completion(
        provider=AIProvider.ANTHROPIC,
        model="sonnet",
        content="",
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
        served_model="claude-sonnet-4-6",
    )
    silent = finish_api_completion(
        provider=AIProvider.ANTHROPIC,
        model="sonnet",
        content="",
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
    )

    assert_that([model.name for model in served.reported_models]).is_equal_to(
        ["claude-sonnet-4-6"],
    )
    assert_that(silent.reported_models).is_empty()


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
        calls=[_call("claude-opus-5-5", "claude-opus-5-5"), _call("claude-opus-5-5")],
        first_party=False,
    )

    assert_that(state).is_equal_to(ModelVerification.UNVERIFIABLE)


def test_every_call_reporting_its_requested_model_is_verified() -> None:
    """Aliases normalise to the canonical name before comparison."""
    state = verify_models(
        requested="claude-sonnet-4-6",
        calls=[
            LedgerCall(
                requested="claude-sonnet-4-6",
                reported=(
                    ReportedModel(model="sonnet", canonical="claude-sonnet-4-6"),
                ),
            ),
            _call("claude-sonnet-4-6", "CLAUDE-SONNET-4-6"),
        ],
        first_party=True,
    )

    assert_that(state).is_equal_to(ModelVerification.VERIFIED)


def test_a_declared_fallback_is_verified_against_its_own_request() -> None:
    """A fallback or custom-agent call asks for another allowed model."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[
            _call("claude-opus-5-5", "claude-opus-5-5"),
            _call("claude-sonnet-4-6", "claude-sonnet-4-6"),
        ],
        first_party=True,
        allowed=("claude-opus-5-5", "claude-sonnet-4-6"),
    )

    assert_that(state).is_equal_to(ModelVerification.VERIFIED)


def test_a_call_requesting_a_model_the_lane_forbids_is_a_mismatch() -> None:
    """The lane's allowed list bounds what may be asked for, not only answered."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[_call("claude-sonnet-4-6", "claude-sonnet-4-6")],
        first_party=True,
        allowed=("claude-opus-5-5",),
    )

    assert_that(state).is_equal_to(ModelVerification.MISMATCH)


def test_a_call_that_answered_with_another_model_is_a_mismatch() -> None:
    """One call off its requested model fails the round."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[
            _call("claude-opus-5-5", "claude-opus-5-5"),
            _call("claude-opus-5-5", "glm-5.3-flash"),
        ],
        first_party=True,
    )

    assert_that(state).is_equal_to(ModelVerification.MISMATCH)


def test_a_helper_model_outside_the_allowed_list_is_a_mismatch() -> None:
    """Every reported model must be one the lane allows."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[_call("claude-opus-5-5", "claude-opus-5-5", "claude-haiku-4-5")],
        first_party=True,
    )

    assert_that(state).is_equal_to(ModelVerification.MISMATCH)


def test_an_allowed_helper_model_keeps_the_round_verified() -> None:
    """The lane row's allowed models widen what a call may report."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[_call("claude-opus-5-5", "claude-opus-5-5", "claude-haiku-4-5")],
        first_party=True,
        allowed=("claude-opus-5-5", "claude-haiku-4-5"),
    )

    assert_that(state).is_equal_to(ModelVerification.VERIFIED)


def test_mismatch_outranks_unreported_which_outranks_verified() -> None:
    """The states are assigned in the ADR's fixed order over every call."""
    unreported = verify_models(
        requested="claude-opus-5-5",
        calls=[_call("claude-opus-5-5", "claude-opus-5-5"), _call("claude-opus-5-5")],
        first_party=True,
    )
    mismatch = verify_models(
        requested="claude-opus-5-5",
        calls=[_call("claude-opus-5-5"), _call("claude-opus-5-5", "glm-5.3-flash")],
        first_party=True,
    )

    assert_that(unreported).is_equal_to(ModelVerification.UNREPORTED)
    assert_that(mismatch).is_equal_to(ModelVerification.MISMATCH)


# --- ledger ------------------------------------------------------------------


def test_the_ledger_keeps_each_calls_request_and_distinct_models() -> None:
    """``reported_models`` is a set in order; ``cost_reported`` is sticky."""
    ledger = ModelLedger()
    ledger.record(
        AIResponse(content="", model="m", reported_models=_reported("a", "b")),
    )
    ledger.record(
        AIResponse(
            content="",
            model="fallback",
            reported_models=_reported("b", "c"),
            cost_reported=True,
        ),
    )
    ledger.record(AIResponse(content="", model="m"))

    assert_that([model.name for model in ledger.reported_models]).is_equal_to(
        ["a", "b", "c"],
    )
    assert_that([call.requested for call in ledger.calls]).is_equal_to(
        ["m", "fallback", "m"],
    )
    assert_that(ledger.calls[2].reported).is_empty()
    assert_that(ledger.cost_reported).is_true()


def test_no_ledger_is_open_outside_the_scope() -> None:
    """The context manager owns the ledger's lifetime."""
    assert_that(current_model_ledger()).is_none()
    with model_ledger() as ledger:
        assert_that(current_model_ledger()).is_same_as(ledger)
    assert_that(current_model_ledger()).is_none()


def _ledger_provider() -> MagicMock:
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.complete = AsyncMock(
        return_value=AIResponse(
            content="ok",
            model="claude-sonnet-4-6",
            cost_estimate=0.01,
            reported_models=_reported("claude-sonnet-4-6"),
            cost_reported=True,
        ),
    )
    return provider


@pytest.mark.parametrize("capped", [False, True])
async def test_call_ai_reports_each_completed_call_to_the_open_ledger(
    capped: bool,
) -> None:
    """The one seam every provider call crosses feeds the ledger, capped or not."""
    provider = _ledger_provider()
    config = AIConfig(enabled=True, transport=AITransport.API, max_retries=0)
    budget = CostBudget(max_cost_usd=1.0) if capped else None

    with model_ledger() as ledger:
        for prompt in ("hello", "again"):
            await call_ai(
                provider=provider,
                ai_config=config,
                user_prompt=prompt,
                system_prompt=None,
                budget=budget,
            )

    assert_that(ledger.calls).is_length(2)
    assert_that([model.name for model in ledger.reported_models]).is_equal_to(
        ["claude-sonnet-4-6"],
    )
    assert_that(ledger.cost_reported).is_true()
    if budget is not None:
        assert_that(budget.spent).is_close_to(0.02, 1e-9)


# --- recorded_cost_basis -----------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "first_party", "cost_reported", "expected"),
    [
        (CostBasis.BILLED, True, False, CostBasis.BILLED),
        (CostBasis.BILLED, False, False, CostBasis.UNKNOWN),
        (CostBasis.ESTIMATED, False, True, CostBasis.UNKNOWN),
        (CostBasis.ESTIMATED, True, True, CostBasis.ESTIMATED),
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
    """Off the provider's host every figure is unknown; on it, the envelope decides."""
    basis = recorded_cost_basis(
        configured=configured,
        first_party=first_party,
        cost_reported=cost_reported,
    )

    assert_that(basis).is_equal_to(expected)


# --- transport profile --------------------------------------------------------


@pytest.fixture
def _isolated_profile_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every provenance input the profile reads at the test's temp dir."""
    for name in (
        "ANTHROPIC_API_KEY",
        "LINTRO_CLI_BARE",
        ANTHROPIC_AUTH_TOKEN_ENV,
        ANTHROPIC_BASE_URL_ENV,
        CLAUDE_OAUTH_TOKEN_ENV,
        REVIEW_LANE_ENV,
        REVIEW_ENDPOINT_ENV,
        REVIEW_FIRST_PARTY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in _env(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.mark.usefixtures("_isolated_profile_env")
def test_a_gateway_cli_run_resolves_an_enforced_unknown_basis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile says where a subscription run is going before it starts."""
    monkeypatch.setenv(ANTHROPIC_BASE_URL_ENV, "https://api.z.ai/api/anthropic")
    monkeypatch.setenv(ANTHROPIC_AUTH_TOKEN_ENV, "zai-token")

    resolved = resolve_transport_settings(
        AIConfig(transport=AITransport.CLI, provider=AIProvider.ANTHROPIC),
    )

    assert_that(resolved.auth_mode).is_equal_to("gateway_token")
    assert_that(resolved.cost_basis).is_equal_to(CostBasis.UNKNOWN)
    assert_that(resolved.cost_is_advisory).is_false()


@pytest.mark.usefixtures("_isolated_profile_env")
def test_a_bare_run_off_the_providers_host_is_unknown_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An API key billed through a proxy is priced on the wrong table as well."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv(ANTHROPIC_BASE_URL_ENV, "https://proxy.example/anthropic")

    resolved = resolve_transport_settings(
        AIConfig(transport=AITransport.CLI, provider=AIProvider.ANTHROPIC),
    )

    assert_that(resolved.cost_basis).is_equal_to(CostBasis.UNKNOWN)


@pytest.mark.usefixtures("_isolated_profile_env")
def test_an_api_transport_gateway_is_unknown_with_a_gateway_token() -> None:
    """``ai.api_base_url`` is the API transport's lane switch."""
    resolved = resolve_transport_settings(
        AIConfig(
            transport=AITransport.API,
            provider=AIProvider.ANTHROPIC,
            api_base_url="https://api.z.ai/api/anthropic",
        ),
    )

    assert_that(resolved.cost_basis).is_equal_to(CostBasis.UNKNOWN)
    assert_that(resolved.auth_mode).is_equal_to("gateway_token")


@pytest.mark.usefixtures("_isolated_profile_env")
def test_a_first_party_api_transport_stays_billed() -> None:
    """The default API path is unchanged."""
    resolved = resolve_transport_settings(
        AIConfig(transport=AITransport.API, provider=AIProvider.ANTHROPIC),
    )

    assert_that(resolved.cost_basis).is_equal_to(CostBasis.BILLED)
    assert_that(resolved.auth_mode).is_equal_to("api_key")


# --- round-two review findings ------------------------------------------------


def test_an_allowed_alias_cannot_vouch_for_a_different_canonical_model() -> None:
    """Only the canonical name counts for the allowed check (ADR-0011 B)."""
    state = verify_models(
        requested="claude-opus-5-5",
        calls=[
            LedgerCall(
                requested="claude-opus-5-5",
                reported=(ReportedModel(model="claude-opus-5-5", canonical="glm-5"),),
            ),
        ],
        first_party=True,
        allowed=("claude-opus-5-5",),
    )

    assert_that(state).is_equal_to(ModelVerification.MISMATCH)


def test_the_api_transport_honours_the_sdks_own_base_url_variable(
    tmp_path: Path,
) -> None:
    """Without ``ai.api_base_url`` the SDK reads the provider's variable itself."""
    provenance = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
        env=_env(tmp_path, **{ANTHROPIC_BASE_URL_ENV: "https://gateway.invalid/v1"}),
        home=tmp_path,
    )

    assert_that(provenance.endpoint).is_equal_to("gateway.invalid")
    assert_that(provenance.first_party).is_false()
    assert_that(provenance.credential).is_equal_to(CredentialKind.ZAI_TOKEN)


def test_a_first_party_claim_cannot_override_a_contradicting_host(
    tmp_path: Path,
) -> None:
    """A lane row saying first-party while the CLI points at a gateway fails closed."""
    provenance = _detect(
        tmp_path,
        **{
            REVIEW_LANE_ENV: "claude-subscription",
            REVIEW_FIRST_PARTY_ENV: "true",
            ANTHROPIC_BASE_URL_ENV: "https://api.z.ai/api/anthropic",
        },
    )

    assert_that(provenance.first_party).is_false()


def test_an_api_key_in_the_settings_env_block_is_a_key(tmp_path: Path) -> None:
    """The key is read where the CLI reads it, like the token and the base URL."""
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-ant"}}),
        encoding="utf-8",
    )

    provenance = _detect(tmp_path, **{CLAUDE_OAUTH_TOKEN_ENV: "tok"})

    assert_that(provenance.credential).is_equal_to(CredentialKind.API_KEY)


@pytest.mark.parametrize(
    ("requested", "served", "canonical"),
    [
        ("gpt-5", "gpt-5-2026-01-01", "gpt-5"),
        ("claude-sonnet-4", "claude-sonnet-4-20250514", "claude-sonnet-4"),
        ("gpt-5", "gpt-5-mini-2026-01-01", "gpt-5-mini-2026-01-01"),
        ("claude-opus-5-5", "claude-opus-5-5", "claude-opus-5-5"),
    ],
)
def test_a_dated_snapshot_of_the_requested_alias_is_the_same_model(
    requested: str,
    served: str,
    canonical: str,
) -> None:
    """A vendor answering ``gpt-5`` with ``gpt-5-<date>`` served what was asked."""
    response = finish_api_completion(
        provider=AIProvider.OPENAI,
        model=requested,
        content="",
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
        served_model=served,
    )

    assert_that(response.reported_models[0].model).is_equal_to(served)
    assert_that(response.reported_models[0].canonical).is_equal_to(canonical)
    assert_that(
        verify_models(
            requested=requested,
            calls=[LedgerCall(requested=requested, reported=response.reported_models)],
            first_party=True,
        ),
    ).is_equal_to(
        (
            ModelVerification.VERIFIED
            if canonical == requested
            else ModelVerification.MISMATCH
        ),
    )


async def test_a_gateway_runs_calls_are_priced_on_the_shadow_model_before_the_budget() -> (
    None
):
    """The budget settles the shadow figure, not the envelope's (ADR-0011 B)."""
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.complete = AsyncMock(
        return_value=AIResponse(
            content="ok",
            model="claude-sonnet-4-20250514",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cost_estimate=0.02,
            cost_reported=True,
        ),
    )
    config = AIConfig(enabled=True, transport=AITransport.API, max_retries=0)
    budget = CostBudget(max_cost_usd=100.0)

    with model_ledger(shadow_price_model="claude-sonnet-4-20250514"):
        response = await call_ai(
            provider=provider,
            ai_config=config,
            user_prompt="hello",
            system_prompt=None,
            budget=budget,
        )

    # Sonnet's list table: $3 per million in, $15 per million out.
    assert_that(response.cost_estimate).is_close_to(18.0, 1e-9)
    assert_that(budget.spent).is_close_to(18.0, 1e-9)


async def test_a_first_party_runs_figures_stand_as_reported() -> None:
    """Without a shadow model the ledger leaves the figure alone."""
    provider = _ledger_provider()
    config = AIConfig(enabled=True, transport=AITransport.API, max_retries=0)

    with model_ledger():
        response = await call_ai(
            provider=provider,
            ai_config=config,
            user_prompt="hello",
            system_prompt=None,
            budget=None,
        )

    assert_that(response.cost_estimate).is_equal_to(0.01)


def test_project_settings_outrank_user_settings_for_the_base_url(
    tmp_path: Path,
) -> None:
    """Claude Code's precedence: the project file overrides the user file."""
    env = _env(tmp_path)
    user_dir = Path(env["CLAUDE_CONFIG_DIR"])
    (user_dir / "settings.json").write_text(
        json.dumps({"env": {ANTHROPIC_BASE_URL_ENV: "https://api.anthropic.com"}}),
        encoding="utf-8",
    )
    project_dir = tmp_path / ".claude"
    project_dir.mkdir()
    (project_dir / "settings.json").write_text(
        json.dumps({"env": {ANTHROPIC_BASE_URL_ENV: "https://api.z.ai/api/anthropic"}}),
        encoding="utf-8",
    )

    provenance = _detect(tmp_path)

    assert_that(provenance.endpoint).is_equal_to("api.z.ai")
    assert_that(provenance.first_party).is_false()


def test_bare_billing_finds_a_project_level_key_helper_in_the_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The workspace, not the process directory, is where the run reads settings."""
    from lintro.ai.transport import cli_bare_billing

    for name, value in _env(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LINTRO_CLI_BARE", raising=False)
    workspace = tmp_path / "repo"
    (workspace / ".claude").mkdir(parents=True)
    (workspace / ".claude" / "settings.json").write_text(
        json.dumps({"apiKeyHelper": "/bin/echo key"}),
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config = AIConfig(transport=AITransport.CLI, provider=AIProvider.ANTHROPIC)

    assert_that(cli_bare_billing(config, cwd=str(workspace))).is_true()
    assert_that(cli_bare_billing(config)).is_false()


async def test_a_turn_limited_gateway_call_is_charged_on_the_shadow_model() -> None:
    """The stopped call's tokens are priced like a completed call's (ADR-0011 B)."""
    from lintro.ai.exceptions import AITurnLimitError

    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.complete = AsyncMock(
        side_effect=AITurnLimitError(
            "stopped",
            input_tokens=1_000_000,
            output_tokens=0,
            cost_estimate=0.02,
        ),
    )
    config = AIConfig(enabled=True, transport=AITransport.API, max_retries=0)
    budget = CostBudget(max_cost_usd=100.0)

    with model_ledger(shadow_price_model="claude-sonnet-4-20250514"):
        with pytest.raises(AITurnLimitError) as raised:
            await call_ai(
                provider=provider,
                ai_config=config,
                user_prompt="hello",
                system_prompt=None,
                budget=budget,
            )

    # Sonnet's list table: $3 per million in; one figure on both sides.
    assert_that(budget.spent).is_close_to(3.0, 1e-9)
    assert_that(raised.value.cost_estimate).is_close_to(3.0, 1e-9)


async def test_a_turn_limited_gateway_call_with_no_envelope_figure_is_still_charged() -> (
    None
):
    """A gateway that reports no figure cannot make a stopped call free."""
    from lintro.ai.exceptions import AITurnLimitError

    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.complete = AsyncMock(
        side_effect=AITurnLimitError(
            "stopped",
            input_tokens=1_000_000,
            output_tokens=0,
            cost_estimate=0.0,
        ),
    )
    config = AIConfig(enabled=True, transport=AITransport.API, max_retries=0)
    budget = CostBudget(max_cost_usd=100.0)

    with model_ledger(shadow_price_model="claude-sonnet-4-20250514"):
        with pytest.raises(AITurnLimitError):
            await call_ai(
                provider=provider,
                ai_config=config,
                user_prompt="hello",
                system_prompt=None,
                budget=budget,
            )

    assert_that(budget.spent).is_close_to(3.0, 1e-9)


async def test_a_zero_rate_shadow_model_cannot_make_the_cap_free() -> None:
    """Cursor's ``auto`` carries zero rates; the shadow falls back to the default table."""
    provider = MagicMock()
    provider.model_name = "auto"
    provider.complete = AsyncMock(
        return_value=AIResponse(
            content="ok",
            model="auto",
            input_tokens=1_000_000,
            output_tokens=0,
            cost_estimate=0.5,
        ),
    )
    config = AIConfig(enabled=True, transport=AITransport.API, max_retries=0)
    budget = CostBudget(max_cost_usd=100.0)

    with model_ledger(shadow_price_model="auto"):
        response = await call_ai(
            provider=provider,
            ai_config=config,
            user_prompt="hello",
            system_prompt=None,
            budget=budget,
        )

    assert_that(response.cost_estimate).is_greater_than(0.0)
    assert_that(budget.spent).is_equal_to(response.cost_estimate)
