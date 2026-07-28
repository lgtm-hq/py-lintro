"""Tests for presence-only liveness probing on CLI transports (#1614).

A subscription agent CLI cannot be probed cheaply — a real invocation is slow and
may consume a metered turn — so its liveness check is limited to the free
capability surface the hybrid guard already reads. The point of these tests is
that the limitation is *declared*: the probe must never report a quota verdict it
did not earn, because a falsely confident "credential is live" is the same defect
as a green check for a review that never ran (#1826).
"""

from __future__ import annotations

import subprocess  # nosec B404 - CompletedProcess objects are constructed to drive the transport under test
from collections.abc import Callable

import pytest
from assertpy import assert_that

from lintro.ai.liveness import LivenessState
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_contracts import CliContract, cli_contract_for
from lintro.ai.providers.cli_transport import CliTransport
from tests.unit.ai.conftest import completed_process as _completed
from tests.unit.ai.conftest import patch_cli_exec

_TEST_CONTRACT = CliContract(
    binary="fake",
    display_name="Fake",
    upgrade_hint="Upgrade the fake CLI.",
    version_floor=(2, 0, 0),
    required_flags=("--always", "--print"),
    optional_flags=cli_contract_for(AIProvider.ANTHROPIC).optional_flags,
)

_FULL_HELP = "Usage: fake [options]\n  --always\n  --print\n  --resume\n"


class _FakeTransport(CliTransport):
    """Minimal concrete transport for exercising the liveness probe."""

    def parse_stdout(self, stdout: str) -> str:
        """Return stdout unchanged.

        Args:
            stdout: Raw stdout from the CLI.

        Returns:
            The unmodified stdout.
        """
        return stdout


def _transport() -> _FakeTransport:
    """Return a guarded transport backed by the test contract.

    Returns:
        A transport carrying the test contract.
    """
    return _FakeTransport(
        binary_path="/usr/local/bin/fake",
        binary_name="Fake",
        install_hint="Install the fake CLI.",
        contract=_TEST_CONTRACT,
    )


def _probe_replies(
    *,
    version: str,
    help_text: str,
    help_returncode: int = 0,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a spawn side effect answering version and help probes.

    Args:
        version: Text the ``--version`` probe should print.
        help_text: Text the help probe should print.
        help_returncode: Exit status the help probe should report.

    Returns:
        A callable suitable as ``patch_cli_exec`` ``side_effect``.
    """

    def _reply(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "--version" in cmd:
            return _completed(stdout=version)
        return _completed(stdout=help_text, returncode=help_returncode)

    return _reply


async def test_conforming_cli_is_live_but_not_quota_verified() -> None:
    """A conforming CLI is usable, and says outright that quota is unchecked."""
    with patch_cli_exec(
        side_effect=_probe_replies(version="2.5.0", help_text=_FULL_HELP),
    ):
        result = await _transport().probe_liveness(provider_name="anthropic")

    assert_that(result.state).is_equal_to(LivenessState.OK)
    assert_that(result.is_live).is_true()
    assert_that(result.quota_verified).described_as(
        "a presence-only probe must not claim to have verified quota",
    ).is_false()
    assert_that(result.message).contains("presence-only")


async def test_below_floor_cli_is_reported_incompatible() -> None:
    """A binary below the declared version floor cannot serve calls."""
    with patch_cli_exec(
        side_effect=_probe_replies(version="1.9.0", help_text=_FULL_HELP),
    ):
        result = await _transport().probe_liveness(provider_name="anthropic")

    assert_that(result.state).is_equal_to(LivenessState.INCOMPATIBLE_CLI)
    assert_that(result.is_live).is_false()
    assert_that(result.message).contains("older than the minimum supported")


async def test_missing_required_flag_is_reported_incompatible() -> None:
    """A vanished required flag is surfaced instead of failing at call time."""
    partial_help = "Usage: fake [options]\n  --print\n"
    with patch_cli_exec(
        side_effect=_probe_replies(version="2.5.0", help_text=partial_help),
    ):
        result = await _transport().probe_liveness(provider_name="anthropic")

    assert_that(result.state).is_equal_to(LivenessState.INCOMPATIBLE_CLI)
    assert_that(result.message).contains("--always")
    assert_that(result.hint).contains("Upgrade the fake CLI")


async def test_unreadable_help_is_not_treated_as_missing_flags() -> None:
    """An unreadable help surface is absence of evidence, not evidence of absence.

    The guard's reactive backstop still protects the call, so refusing to serve
    here would be a false negative.
    """
    with patch_cli_exec(
        side_effect=_probe_replies(
            version="2.5.0",
            help_text="fake: unexpected error",
            help_returncode=1,
        ),
    ):
        transport = _transport()
        missing = await transport.missing_required_flags()
        result = await transport.probe_liveness(provider_name="anthropic")

    assert_that(missing).is_empty()
    assert_that(result.state).is_equal_to(LivenessState.OK)


async def test_missing_required_flags_is_inert_without_a_contract() -> None:
    """An unguarded transport declares nothing, so it can report nothing."""
    transport = _FakeTransport(
        binary_path="/usr/local/bin/fake",
        binary_name="Fake",
        install_hint="Install the fake CLI.",
    )
    with patch_cli_exec(return_value=_completed(stdout=_FULL_HELP)):
        assert_that(await transport.missing_required_flags()).is_empty()


@pytest.mark.parametrize("provider", list(AIProvider))
async def test_cli_backed_providers_expose_their_transport(
    *,
    provider: AIProvider,
) -> None:
    """Liveness branches on transport centrally, so the hook must be wired.

    Args:
        provider: Provider whose CLI transport hook is checked.
    """
    from lintro.ai.config import AIConfig
    from lintro.ai.enums import AITransport
    from lintro.ai.providers import get_provider

    try:
        instance = get_provider(
            AIConfig(enabled=True, provider=provider, transport=AITransport.CLI),
        )
    except Exception:  # noqa: BLE001 - CLI absent on this machine; nothing to wire
        pytest.skip(f"{provider.value} CLI transport not constructible here")

    assert_that(instance._cli_transport()).is_not_none()
