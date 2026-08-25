"""Tests for the hybrid capability guard on CLI transports (#1612)."""

from __future__ import annotations

import asyncio
import os
import subprocess  # nosec B404 - CompletedProcess objects are constructed to drive the transport under test
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import AINotAvailableError, AIProviderError
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_contracts import (
    CLI_CONTRACTS,
    CliContract,
    cli_contract_for,
    format_version,
)
from lintro.ai.providers.cli_transport import CliTransport, OptionalArg
from tests.unit.ai.conftest import (
    HANG,
    patch_cli_exec,
)
from tests.unit.ai.conftest import (
    completed_process as _completed,
)

_TEST_CONTRACT = CliContract(
    binary="fake",
    display_name="Fake",
    upgrade_hint="Upgrade the fake CLI.",
    version_floor=(2, 0, 0),
    required_flags=("--always",),
    # Reuse the real Anthropic optional flags so the degradation warning has
    # declared purpose text to draw on, exactly as in production.
    optional_flags=cli_contract_for(AIProvider.ANTHROPIC).optional_flags,
)


class _FakeTransport(CliTransport):
    """Minimal concrete transport for exercising the guard."""

    def parse_stdout(self, stdout: str) -> str:
        """Return stdout unchanged.

        Args:
            stdout: Raw stdout from the CLI.

        Returns:
            The unmodified stdout.
        """
        return stdout


def _is_probe(cmd: list[str]) -> bool:
    """Return whether an argv is a free capability probe.

    Args:
        cmd: Argv the transport invoked.

    Returns:
        True for ``--version`` / ``--help`` probes.
    """
    return "--version" in cmd or "--help" in cmd


@pytest.fixture()
def transport() -> _FakeTransport:
    """Return a guarded transport backed by the test contract.

    Returns:
        A ``_FakeTransport`` carrying ``_TEST_CONTRACT``.
    """
    return _FakeTransport(
        binary_path="/usr/local/bin/fake",
        binary_name="Fake",
        install_hint="Install the fake CLI.",
        contract=_TEST_CONTRACT,
    )


# -- Version parsing --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2.1.218 (Claude Code)", (2, 1, 218)),
        ("2026.07.09-a3815c0", (2026, 7, 9)),
        ("codex-cli 0.20.0", (0, 20, 0)),
        ("v1.4.9\n", (1, 4, 9)),
        ("", None),
        ("no version here", None),
        # A bare decimal must not be mistaken for a version.
        ('{"total_cost_usd": 0.01}', None),
    ],
)
def test_parse_version(text: str, expected: tuple[int, ...] | None) -> None:
    """Parse semver and calendar versions while rejecting incidental decimals."""
    assert_that(CliTransport.parse_version(text)).is_equal_to(expected)


def test_format_version_renders_unknown() -> None:
    """Render a missing version as 'unknown' and a known one as dotted."""
    assert_that(format_version(None)).is_equal_to("unknown")
    assert_that(format_version((2, 1, 218))).is_equal_to("2.1.218")


async def test_binary_version_probes_once(transport: _FakeTransport) -> None:
    """Cache the version probe so repeated calls spawn one subprocess."""
    with patch_cli_exec(return_value=_completed(stdout="3.1.0")) as mock_run:
        assert_that(await transport.binary_version()).is_equal_to((3, 1, 0))
        assert_that(await transport.binary_version()).is_equal_to((3, 1, 0))

    assert_that(mock_run.call_count).is_equal_to(1)


async def test_binary_version_none_when_probe_fails(transport: _FakeTransport) -> None:
    """Report an unknown version when the probe cannot be spawned."""
    with patch_cli_exec(side_effect=PermissionError()):
        assert_that(await transport.binary_version()).is_none()


async def test_binary_version_none_on_nonzero_probe(transport: _FakeTransport) -> None:
    """Distrust a non-zero --version exit."""
    with patch_cli_exec(return_value=_completed(returncode=1, stdout="9.9.9")):
        assert_that(await transport.binary_version()).is_none()


# -- Version floor ----------------------------------------------------------


async def test_check_version_floor_raises_below_floor(
    transport: _FakeTransport,
) -> None:
    """Raise an actionable error when the binary predates the floor."""
    with (
        patch_cli_exec(return_value=_completed(stdout="1.9.9")),
        pytest.raises(AINotAvailableError) as excinfo,
    ):
        await transport.check_version_floor()

    message = str(excinfo.value)
    assert_that(message).contains("1.9.9")
    assert_that(message).contains("2.0.0")
    assert_that(message).contains("Upgrade the fake CLI.")


async def test_check_version_floor_accepts_floor_exactly(
    transport: _FakeTransport,
) -> None:
    """Accept a binary sitting exactly on the floor."""
    with patch_cli_exec(return_value=_completed(stdout="2.0.0")):
        await transport.check_version_floor()


async def test_check_version_floor_allows_unknown_version(
    transport: _FakeTransport,
) -> None:
    """Never block on an unreadable version -- the other guards still apply."""
    with patch_cli_exec(return_value=_completed(stdout="mystery build")):
        await transport.check_version_floor()


async def test_check_version_floor_is_inert_without_contract() -> None:
    """Skip the floor check entirely for transports with no contract."""
    unguarded = _FakeTransport(
        binary_path="/usr/local/bin/fake",
        binary_name="Fake",
        install_hint="Install the fake CLI.",
    )
    with patch_cli_exec(return_value=_completed()) as mock_run:
        await unguarded.check_version_floor()

    assert_that(mock_run.call_count).is_equal_to(0)


# -- Proactive --help gate --------------------------------------------------


async def test_supports_flag_true_when_advertised(transport: _FakeTransport) -> None:
    """Report a flag supported when the help text advertises it."""
    help_text = "  --resume <id>  Resume a session\n"
    with patch_cli_exec(return_value=_completed(stdout=help_text)):
        assert_that(await transport.supports_flag("--resume")).is_true()


async def test_supports_flag_false_when_absent(transport: _FakeTransport) -> None:
    """Report a flag unsupported when readable help text omits it."""
    help_text = "  --json-schema <schema>  Provide a JSON schema\n"
    with patch_cli_exec(return_value=_completed(stdout=help_text)):
        assert_that(await transport.supports_flag("--json-schema-name")).is_false()


async def test_supports_flag_optimistic_on_nonzero_help(
    transport: _FakeTransport,
) -> None:
    """Fall back to the backstop when --help exits non-zero."""
    result = _completed(returncode=1, stderr="error near --json-schema-name")
    with patch_cli_exec(return_value=result):
        assert_that(await transport.supports_flag("--json-schema-name")).is_true()


async def test_supports_flag_optimistic_when_probe_fails(
    transport: _FakeTransport,
) -> None:
    """Send the flag when help cannot be read, leaving it to the backstop."""
    with patch_cli_exec(side_effect=PermissionError()):
        assert_that(await transport.supports_flag("--resume")).is_true()


async def test_supports_flag_does_not_match_on_prefix(
    transport: _FakeTransport,
) -> None:
    """A flag is not deemed supported by appearing inside a longer flag name.

    Help advertising only ``--json-schema-name`` must not report the prefix
    ``--json-schema`` as supported.
    """
    help_text = "  --json-schema-name <name>  Name the structured schema\n"
    with patch_cli_exec(return_value=_completed(stdout=help_text)):
        assert_that(await transport.supports_flag("--json-schema")).is_false()
        assert_that(await transport.supports_flag("--json-schema-name")).is_true()


async def test_supports_flag_caches_help_probe(transport: _FakeTransport) -> None:
    """Probe --help once no matter how many flags are queried."""
    help_text = "  --resume <id>\n  --json-schema-name <name>\n"
    with patch_cli_exec(return_value=_completed(stdout=help_text)) as mock_run:
        assert_that(await transport.supports_flag("--resume")).is_true()
        assert_that(await transport.supports_flag("--json-schema-name")).is_true()

    assert_that(mock_run.call_count).is_equal_to(1)


async def test_filter_optional_args_drops_unadvertised(
    transport: _FakeTransport,
) -> None:
    """Keep only optional args the installed binary advertises."""
    help_text = "  --resume <id>  Resume a session\n"
    candidates = [
        OptionalArg(flag="--json-schema-name", values=("lintro_review",)),
        OptionalArg(flag="--resume", values=("abc123",)),
    ]
    with patch_cli_exec(return_value=_completed(stdout=help_text)):
        kept = await transport.filter_optional_args(candidates)

    assert_that([arg.flag for arg in kept]).is_equal_to(["--resume"])


def test_optional_arg_as_argv() -> None:
    """Flatten an optional arg into argv tokens."""
    arg = OptionalArg(flag="--resume", values=("abc123",))
    assert_that(arg.as_argv()).is_equal_to(["--resume", "abc123"])


# -- Reactive backstop ------------------------------------------------------


async def test_run_guarded_retries_without_rejected_flag(
    transport: _FakeTransport,
) -> None:
    """Drop a rejected optional flag and retry rather than failing the call."""
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if _is_probe(cmd):
            return _completed(stdout="3.0.0", args=cmd)
        if "--json-schema-name" in cmd:
            return _completed(
                returncode=1,
                stderr="error: unknown option '--json-schema-name'",
                args=cmd,
            )
        return _completed(stdout="done", args=cmd)

    optional_args = [
        OptionalArg(flag="--json-schema-name", values=("lintro_review",)),
    ]
    cmd = [
        "/usr/local/bin/fake",
        "--always",
        "--json-schema-name",
        "lintro_review",
        "--model",
        "m",
    ]
    with patch_cli_exec(side_effect=fake_run):
        result = await transport.run_guarded(
            cmd,
            optional_args=optional_args,
            timeout=30.0,
        )

    assert_that(result.returncode).is_equal_to(0)
    retried = calls[-1]
    assert_that(retried).does_not_contain("--json-schema-name")
    assert_that(retried).does_not_contain("lintro_review")
    assert_that(retried).contains("--always")
    assert_that(retried).contains("--model")
    assert_that(retried).contains("m")


async def test_run_guarded_remembers_rejected_flag(transport: _FakeTransport) -> None:
    """Cache a backstop rejection so the flag is not sent again."""

    def fake_run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if _is_probe(cmd):
            return _completed(stdout="3.0.0", args=cmd)
        if "--resume" in cmd:
            return _completed(
                returncode=1,
                stderr="error: unknown option '--resume'",
                args=cmd,
            )
        return _completed(stdout="done", args=cmd)

    optional_args = [OptionalArg(flag="--resume", values=("abc123",))]
    with patch_cli_exec(side_effect=fake_run):
        await transport.run_guarded(
            ["/usr/local/bin/fake", "--resume", "abc123"],
            optional_args=optional_args,
            timeout=30.0,
        )
        assert_that(await transport.supports_flag("--resume")).is_false()


async def test_run_guarded_drops_flags_one_at_a_time(transport: _FakeTransport) -> None:
    """Peel off each rejected optional flag until the call succeeds."""
    rejected: list[str] = []

    def fake_run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if _is_probe(cmd):
            return _completed(stdout="3.0.0", args=cmd)
        for flag in ("--json-schema-name", "--resume"):
            if flag in cmd:
                rejected.append(flag)
                return _completed(
                    returncode=1,
                    stderr=f"error: unknown option '{flag}'",
                    args=cmd,
                )
        return _completed(stdout="done", args=cmd)

    optional_args = [
        OptionalArg(flag="--json-schema-name", values=("lintro_review",)),
        OptionalArg(flag="--resume", values=("abc123",)),
    ]
    cmd = [
        "/usr/local/bin/fake",
        "--json-schema-name",
        "lintro_review",
        "--resume",
        "abc123",
    ]
    with patch_cli_exec(side_effect=fake_run):
        result = await transport.run_guarded(
            cmd,
            optional_args=optional_args,
            timeout=30.0,
        )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(rejected).is_equal_to(["--json-schema-name", "--resume"])


async def test_run_guarded_returns_unrelated_failure(transport: _FakeTransport) -> None:
    """Leave non-flag failures alone so callers map them to real errors."""
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if _is_probe(cmd):
            return _completed(stdout="3.0.0", args=cmd)
        return _completed(
            returncode=1,
            stderr="Authentication required. Please login.",
            args=cmd,
        )

    optional_args = [OptionalArg(flag="--resume", values=("abc123",))]
    with patch_cli_exec(side_effect=fake_run):
        result = await transport.run_guarded(
            ["/usr/local/bin/fake", "--resume", "abc123"],
            optional_args=optional_args,
            timeout=30.0,
        )

    assert_that(result.returncode).is_equal_to(1)
    completion_calls = [call for call in calls if not _is_probe(call)]
    assert_that(completion_calls).is_length(1)


async def test_run_guarded_ignores_unknown_option_for_required_flag(
    transport: _FakeTransport,
) -> None:
    """Do not retry when the rejected flag is not a declared optional one."""

    def fake_run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if _is_probe(cmd):
            return _completed(stdout="3.0.0", args=cmd)
        return _completed(
            returncode=1,
            stderr="error: unknown option '--always'",
            args=cmd,
        )

    with patch_cli_exec(side_effect=fake_run):
        result = await transport.run_guarded(
            ["/usr/local/bin/fake", "--always"],
            optional_args=[],
            timeout=30.0,
        )

    assert_that(result.returncode).is_equal_to(1)


async def test_run_guarded_enforces_version_floor(transport: _FakeTransport) -> None:
    """Refuse to invoke a binary below the declared floor."""
    with (
        patch_cli_exec(return_value=_completed(stdout="1.0.0")),
        pytest.raises(AINotAvailableError),
    ):
        await transport.run_guarded(
            ["/usr/local/bin/fake", "--always"],
            timeout=30.0,
        )


async def test_run_guarded_matches_flag_on_token_boundary(
    transport: _FakeTransport,
) -> None:
    """Drop only the rejected flag, not a candidate that is its prefix.

    A rejection of ``--json-schema-name`` must not also strip a candidate
    ``--json-schema``: substring matching would wrongly drop the shorter flag.
    """
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if _is_probe(cmd):
            return _completed(stdout="3.0.0", args=cmd)
        if "--json-schema-name" in cmd:
            return _completed(
                returncode=1,
                stderr="error: unknown option '--json-schema-name'",
                args=cmd,
            )
        return _completed(stdout="done", args=cmd)

    optional_args = [
        OptionalArg(flag="--json-schema", values=("{}",)),
        OptionalArg(flag="--json-schema-name", values=("lintro_review",)),
    ]
    cmd = [
        "/usr/local/bin/fake",
        "--json-schema",
        "{}",
        "--json-schema-name",
        "lintro_review",
    ]
    with patch_cli_exec(side_effect=fake_run):
        result = await transport.run_guarded(
            cmd,
            optional_args=optional_args,
            timeout=30.0,
        )

    assert_that(result.returncode).is_equal_to(0)
    # The retry drops only the exact rejected flag; its prefix survives.
    retried = [call for call in calls if not _is_probe(call)][-1]
    assert_that(retried).does_not_contain("--json-schema-name")
    assert_that(retried).contains("--json-schema", "{}")


# -- Subprocess execution ---------------------------------------------------


async def test_run_starts_child_in_new_session(transport: _FakeTransport) -> None:
    """Agent children must not share lintro's process group (#2156)."""
    process = MagicMock()
    process.communicate = AsyncMock(return_value=(b'{"ok": true}', b""))
    process.returncode = 0
    process.pid = 4242

    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    ) as spawn:
        await transport.run(["/usr/local/bin/fake", "--always"], timeout=5.0)

    kwargs = spawn.call_args.kwargs
    if os.name == "posix":
        assert_that(kwargs.get("start_new_session")).is_true()
    else:
        assert_that(kwargs).does_not_contain_key("start_new_session")


async def test_run_raises_provider_error_on_timeout(transport: _FakeTransport) -> None:
    """Map a hung child process to an AIProviderError naming the timeout."""
    with (
        patch_cli_exec(return_value=HANG),
        pytest.raises(AIProviderError, match="timed out after 0s"),
    ):
        await transport.run(["/usr/local/bin/fake", "--always"], timeout=0.01)


async def test_run_raises_not_available_when_binary_missing(
    transport: _FakeTransport,
) -> None:
    """Map a failed spawn to an actionable AINotAvailableError."""
    with (
        patch_cli_exec(side_effect=FileNotFoundError()),
        pytest.raises(AINotAvailableError, match="Install the fake CLI."),
    ):
        await transport.run(["/usr/local/bin/fake", "--always"], timeout=5.0)


async def test_run_maps_e2big_to_provider_error(transport: _FakeTransport) -> None:
    """Map OSError(E2BIG) to an actionable AIProviderError (#1967)."""
    import errno

    with (
        patch_cli_exec(side_effect=OSError(errno.E2BIG, "Argument list too long")),
        pytest.raises(AIProviderError, match="E2BIG"),
    ):
        await transport.run(["/usr/local/bin/fake", "--always"], timeout=5.0)


async def test_run_kills_child_when_cancelled(transport: _FakeTransport) -> None:
    """Cancelling a call stops the child instead of orphaning the CLI."""
    with patch_cli_exec(return_value=HANG) as mock_run:
        task = asyncio.ensure_future(
            transport.run(["/usr/local/bin/fake", "--always"], timeout=60.0),
        )
        # Let the spawn happen before cancelling so a child exists to kill.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    assert_that(mock_run.processes).is_length(1)
    assert_that(mock_run.processes[0].killed).is_true()


async def test_run_decodes_stdout_and_stderr(transport: _FakeTransport) -> None:
    """Return decoded stdout/stderr and the child exit code."""
    with patch_cli_exec(
        return_value=_completed(returncode=3, stdout="out", stderr="err"),
    ):
        result = await transport.run(["/usr/local/bin/fake"], timeout=5.0)

    assert_that(result.returncode).is_equal_to(3)
    assert_that(result.stdout).is_equal_to("out")
    assert_that(result.stderr).is_equal_to("err")


# -- Declared contracts -----------------------------------------------------


def test_every_provider_declares_a_cli_contract() -> None:
    """Every provider identity has a declared CLI contract."""
    assert_that(sorted(CLI_CONTRACTS)).is_equal_to(sorted(AIProvider))


@pytest.mark.parametrize("provider", list(AIProvider))
def test_contract_flags_are_distinct_and_well_formed(provider: AIProvider) -> None:
    """Required and optional flag sets are disjoint and use flag syntax."""
    contract = cli_contract_for(provider)
    required = set(contract.required_flags)
    optional = set(contract.optional_flag_names)

    # Every contract must declare at least one required flag; optional flags are
    # legitimately empty for a provider with nothing gracefully-degradable, so
    # only their disjointness and syntax are asserted.
    assert_that(required).is_not_empty()
    assert_that(required.intersection(optional)).is_empty()
    for flag in required | optional:
        assert_that(flag).starts_with("--")
