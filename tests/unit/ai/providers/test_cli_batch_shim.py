"""Agent CLIs on a ``.cmd``/``.bat`` shim never see cmd.exe metacharacters (#3494)."""

from __future__ import annotations

import contextlib
import subprocess  # nosec B404 - CompletedProcess fixtures; one real spawn of sys.executable
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.exceptions import (
    AIBatchShimRefusedError,
    AINotAvailableError,
)
from lintro.ai.fix import generate_fixes
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.liveness import LivenessState
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.anthropic.provider import AnthropicProvider
from lintro.ai.providers.batch_shim import refuse_unsafe_batch_argv
from lintro.ai.providers.cli_contract_check import probe_cli_surface
from lintro.ai.providers.cli_transport import CliTransport
from lintro.ai.providers.cursor.provider import CursorProvider
from lintro.ai.summary import generate_summary
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import (
    CLAUDE_HELP,
    MockAIProvider,
    MockIssue,
    patch_cli_exec,
)

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
    ("binary", "via_cwd"),
    [
        pytest.param(_AGENT_SHIM, True, id="batch-shim"),
        pytest.param("/usr/local/bin/agent", False, id="native-binary"),
    ],
)
async def test_cursor_workspace_path_stays_off_a_batch_command_line(
    tmp_path: Path,
    binary: str,
    via_cwd: bool,
) -> None:
    """On a shim the repository is the child's cwd, not a ``--workspace`` value."""
    repo = tmp_path / "R&D %TEMP%"
    repo.mkdir()
    repo_root = str(repo)
    provider = _cursor(binary)
    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed(_CURSOR_OK)
        response = await provider.complete("Review", repo_root=repo_root)

    assert_that(response.content).is_equal_to("ok")
    call = mock_run.transport_calls[-1]
    assert_that(call.cmd[call.cmd.index("--workspace") + 1]).is_equal_to(
        "." if via_cwd else repo_root,
    )
    assert_that(call.cwd).is_equal_to(repo_root if via_cwd else None)


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


# -- Review round 1 ---------------------------------------------------------


class _RefusedProvider(MockAIProvider):
    """A provider whose CLI is a refused batch shim: every call raises."""

    def __init__(self) -> None:
        super().__init__()
        self.refusals = 0

    async def complete(self, prompt: str, **kwargs: Any) -> Any:
        self.refusals += 1
        raise AIBatchShimRefusedError("claude.cmd refused: --json-schema")


@pytest.mark.parametrize(
    ("layout", "max_workers", "max_calls"),
    [
        pytest.param({"a.py": 1}, 1, 1, id="single-issue"),
        # The batch refusal is not retried issue by issue in single-issue mode.
        pytest.param({"a.py": 2}, 1, 1, id="batch-per-file"),
        pytest.param(
            {"a.py": 1, "b.py": 1, "c.py": 1},
            3,
            3,
            id="concurrent-workers",
        ),
    ],
)
async def test_ai_fix_surfaces_a_refused_provider_instead_of_no_suggestions(
    tmp_path: Path,
    layout: dict[str, int],
    max_workers: int,
    max_calls: int,
) -> None:
    """A refusal reaches the caller; it is not read as "nothing to fix"."""
    issues: list[MockIssue] = []
    for name, count in layout.items():
        source = tmp_path / name
        source.write_text("x = 1\ny = 2\n")
        issues.extend(
            MockIssue(file=str(source), line=line, code="B101", message="m")
            for line in range(1, count + 1)
        )
    provider = _RefusedProvider()

    with pytest.raises(AINotAvailableError, match="--json-schema"):
        await generate_fixes(
            issues,
            provider,
            tool_name="ruff",
            max_workers=max_workers,
            workspace_root=tmp_path,
        )

    assert_that(provider.refusals).is_between(1, max_calls)


async def test_ai_summary_surfaces_a_refused_provider() -> None:
    """The summary call lets the refusal through to the orchestrator."""
    result = ToolResult(
        name="ruff",
        success=True,
        issues_count=1,
        issues=[MockIssue(file="a.py", line=1, message="bad", code="E501")],
    )

    with pytest.raises(AIBatchShimRefusedError, match="claude.cmd"):
        await generate_summary([result], _RefusedProvider())


@pytest.mark.parametrize(
    ("binary", "message_part"),
    [
        pytest.param(_CLAUDE_SHIM, "pass --json-schema", id="schema-flag"),
        pytest.param("C:/R&D/npm/claude.cmd", "whose path holds", id="shim-path"),
    ],
)
async def test_doctor_liveness_reports_a_refused_claude_shim(
    binary: str,
    message_part: str,
) -> None:
    """``lintro doctor`` shows the refusal and the shim remedy, not "ok"."""
    provider = _claude(binary)
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed("claude 2.1.0")
        result = await provider.check_liveness()

    assert_that(result.state).is_equal_to(LivenessState.INCOMPATIBLE_CLI)
    assert_that(result.message).contains(message_part)
    assert_that(result.hint).contains("instead of a .cmd/.bat shim")
    assert_that(result.hint).does_not_contain("npm install")


async def test_shim_under_metacharacter_path_reports_the_path_not_unreadable_help() -> (
    None
):
    """The provider names the shim path instead of an unreadable ``--help``."""
    provider = _claude("C:/R&D/npm/claude.cmd")
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_CLAUDE_OK)
        with pytest.raises(AINotAvailableError) as excinfo:
            await provider.complete("Review", system="Be concise")

    message = str(excinfo.value)
    assert_that(message).contains("whose path holds", "instead of a .cmd/.bat shim")
    assert_that(message).does_not_contain("could not be read")
    assert_that(message).does_not_contain("npm install")


async def test_contract_check_names_why_a_shim_was_not_probed() -> None:
    """The surface report carries the refusal instead of a bare unreadable help."""
    with patch(
        "lintro.ai.providers.cli_contract_check.shutil.which",
        return_value="C:/a&b/claude.cmd",
    ):
        report = await probe_cli_surface(provider=AIProvider.ANTHROPIC)

    assert_that(report.refused_reason).contains("whose path holds")
    assert_that(report.describe()).contains("not probed", "C:/a&b/claude.cmd")
    assert_that(report.help_readable).is_false()


class _EchoTransport(CliTransport):
    """Concrete transport for spawning a real child."""

    def parse_stdout(self, stdout: str, *, model: str) -> Any:
        return stdout


@pytest.mark.parametrize(
    "windows_error",
    [
        pytest.param(None, id="posix-real-spawn"),
        pytest.param(
            NotADirectoryError(267, "The directory name is invalid"),
            id="windows-winerror-267",
        ),
    ],
)
async def test_missing_working_directory_is_not_reported_as_a_missing_binary(
    tmp_path: Path,
    windows_error: OSError | None,
) -> None:
    """A vanished repository root is named, and not as a retried provider error."""
    transport = _EchoTransport(
        binary_path=sys.executable,
        binary_name="Agent",
        install_hint="install it",
    )
    missing = str(tmp_path / "gone")
    spawn = (
        patch(
            "lintro.ai.providers.cli_transport.asyncio.create_subprocess_exec",
            side_effect=windows_error,
        )
        if windows_error is not None
        else contextlib.nullcontext()
    )

    with spawn, pytest.raises(AINotAvailableError) as excinfo:
        await transport.run([sys.executable, "-c", "pass"], timeout=30, cwd=missing)

    assert_that(str(excinfo.value)).contains("working directory", missing)
    assert_that(str(excinfo.value)).does_not_contain("not found on PATH")
