"""Agent CLIs on a ``.cmd``/``.bat`` shim never see cmd.exe metacharacters (#3494)."""

from __future__ import annotations

import subprocess  # nosec B404 - CompletedProcess fixtures only; no process spawn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.exceptions import AINotAvailableError
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.anthropic.provider import AnthropicProvider
from lintro.ai.providers.batch_shim import refuse_unsafe_batch_argv
from lintro.ai.providers.cli_contract_check import _probe
from lintro.ai.providers.cursor.provider import CursorProvider
from tests.unit.ai.conftest import CLAUDE_HELP, patch_cli_exec

#: npm's global shim directory under an ordinary, parenthesised install path.
_SHIM_DIR = "C:/Program Files (x86)/nodejs"
_CLAUDE_SHIM = f"{_SHIM_DIR}/claude.cmd"
_AGENT_SHIM = f"{_SHIM_DIR}/agent.cmd"

_CLAUDE_OK = '{"result":"ok","usage":{"input_tokens":1,"output_tokens":1}}'
_CURSOR_OK = (
    '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
    '"session_id":"s","usage":{"inputTokens":1,"outputTokens":1}}'
)


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _claude(binary: str) -> AnthropicProvider:
    with patch(
        "lintro.ai.providers.anthropic.provider._find_claude",
        return_value=binary,
    ):
        return AnthropicProvider(transport=AITransport.CLI, cli_bare=CliBareMode.NEVER)


def _cursor(binary: str) -> CursorProvider:
    with patch(
        "lintro.ai.providers.cursor.provider._find_agent",
        return_value=binary,
    ):
        return CursorProvider(cursor_trust_workspace=False)


def _spawned_calls(mock_run: MagicMock) -> list[list[str]]:
    """Return argvs that reached a spawn other than a capability probe."""
    probes = {"--help", "--version"}
    return [
        list(call.args[0])
        for call in mock_run.call_args_list
        if not probes.intersection(call.args[0])
    ]


@pytest.mark.parametrize(
    ("system", "cli_schema", "flag"),
    [
        pytest.param(
            "Be concise",
            CliSchemaRequest(schema={"type": "object"}),
            "--json-schema",
            id="schema-json-always-quotes",
        ),
        pytest.param(
            'Reply with "ok" & stop',
            None,
            "--append-system-prompt",
            id="system-prompt-free-text",
        ),
    ],
)
async def test_claude_batch_shim_refuses_metacharacter_argument_before_spawn(
    system: str,
    cli_schema: CliSchemaRequest | None,
    flag: str,
) -> None:
    """A claude.cmd call carrying a quote is refused, naming the flag."""
    provider = _claude(_CLAUDE_SHIM)
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_CLAUDE_OK)
        with pytest.raises(AINotAvailableError) as excinfo:
            await provider.complete("Review", system=system, cli_schema=cli_schema)

    message = str(excinfo.value)
    assert_that(message).contains(f"the value of {flag}", "claude.cmd", "'\"'")
    assert_that(message).does_not_contain(system)
    assert_that(_spawned_calls(mock_run)).is_empty()


@pytest.mark.parametrize(
    ("binary", "schema_sent"),
    [
        pytest.param(_CLAUDE_SHIM, False, id="batch-shim-under-program-files-x86"),
        pytest.param("/usr/local/bin/claude", True, id="native-binary"),
    ],
)
async def test_claude_call_runs_when_no_argument_reaches_cmd_exe(
    binary: str,
    schema_sent: bool,
) -> None:
    """A shim under ``(x86)`` runs; a native binary still gets its schema."""
    provider = _claude(binary)
    cli_schema = CliSchemaRequest(schema={"type": "object"}) if schema_sent else None
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_CLAUDE_OK)
        response = await provider.complete(
            "Review",
            system="Be concise",
            cli_schema=cli_schema,
        )

    assert_that(response.content).is_equal_to("ok")
    (cmd,) = _spawned_calls(mock_run)
    assert_that(cmd[0]).is_equal_to(binary)
    assert_that("--json-schema" in cmd).is_equal_to(schema_sent)
    if schema_sent:
        assert_that(cmd[cmd.index("--json-schema") + 1]).contains('"type"')


@pytest.mark.parametrize(
    ("binary", "workspace_arg", "child_cwd"),
    [
        pytest.param(_AGENT_SHIM, ".", "C:/work/R&D %TEMP%", id="batch-shim"),
        pytest.param(
            "/usr/local/bin/agent",
            "C:/work/R&D %TEMP%",
            None,
            id="native-binary",
        ),
    ],
)
async def test_cursor_workspace_path_stays_off_a_batch_command_line(
    binary: str,
    workspace_arg: str,
    child_cwd: str | None,
) -> None:
    """On a shim the repository is the child's cwd, not a ``--workspace`` value."""
    repo_root = "C:/work/R&D %TEMP%"
    provider = _cursor(binary)
    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed(_CURSOR_OK)
        response = await provider.complete("Review", repo_root=repo_root)

    assert_that(response.content).is_equal_to("ok")
    call = mock_run.transport_calls[-1]
    assert_that(call.cmd[call.cmd.index("--workspace") + 1]).is_equal_to(
        workspace_arg,
    )
    assert_that(call.cwd).is_equal_to(child_cwd)


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        pytest.param(
            ["C:/tools&whoami&/claude.cmd", "--print"],
            "whose path holds characters",
            id="metacharacter-in-shim-directory",
        ),
        pytest.param(
            ["C:/npm/codex.CMD", "--output-schema=C:/Temp/a%b.json"],
            "--output-schema contains '%'",
            id="flag-equals-value",
        ),
        pytest.param(
            ["C:/npm/codex.bat", "exec", "a|b"],
            "argument 2 contains '|'",
            id="positional-argument",
        ),
    ],
)
def test_refuse_unsafe_batch_argv_names_what_cmd_exe_would_interpret(
    cmd: list[str],
    expected: str,
) -> None:
    """The refusal names the shim path, the flag, or the argument position."""
    with pytest.raises(AINotAvailableError, match="executable instead of") as excinfo:
        refuse_unsafe_batch_argv(cmd, binary_name="Agent")

    assert_that(str(excinfo.value)).contains(expected)


@pytest.mark.parametrize(
    "cmd",
    [
        pytest.param(
            ["/usr/local/bin/claude", "--json-schema", '{"a":1}&'],
            id="native",
        ),
        pytest.param([f"{_SHIM_DIR}/claude.cmd", "--tools", ""], id="clean-shim"),
    ],
)
def test_refuse_unsafe_batch_argv_passes_what_cmd_exe_cannot_misread(
    cmd: list[str],
) -> None:
    """A native binary keeps any argument; a clean shim argv is untouched."""
    original = list(cmd)

    refuse_unsafe_batch_argv(cmd, binary_name="Claude")

    assert_that(cmd).is_equal_to(original)


async def test_contract_probe_does_not_run_a_shim_whose_path_cmd_exe_would_parse() -> (
    None
):
    """The contract check reports no help text instead of launching the shim."""
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"  --print\n", b""))
    with patch(
        "lintro.ai.providers.cli_contract_check.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    ):
        refused = await _probe(binary_path="C:/a&b/claude.cmd", args=("--help",))
        allowed = await _probe(binary_path=_CLAUDE_SHIM, args=("--help",))

    assert_that(refused).is_none()
    assert_that(allowed).is_equal_to("  --print\n")
