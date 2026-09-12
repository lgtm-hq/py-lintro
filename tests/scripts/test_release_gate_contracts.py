"""Lockstep tests for the release verify step's couplings to lintro.

``verify_built_binary.sh``, ``drive_interactive_review.py`` and
``drive_mcp_round_trip.py`` classify a release build by matching lintro's own
surface: the CLI's command table, the ``watch`` ready line, the interactive
review prompt and its key bindings, and the MCP server's name, first tool and
protocol revision. Those literals are hand-maintained outside ``lintro``, so
without these tests a change inside ``lintro`` passes the whole suite and only
surfaces as a false packaging failure on the next tag (#2514, #2577).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DRIVER_PATH = _REPO_ROOT / "scripts" / "build" / "drive_interactive_review.py"
_MCP_DRIVER_PATH = _REPO_ROOT / "scripts" / "build" / "drive_mcp_round_trip.py"
_VERIFY_PATH = _REPO_ROOT / "scripts" / "build" / "verify_built_binary.sh"
_WATCHER_PATH = _REPO_ROOT / "lintro" / "watch" / "watcher.py"
_BATS_PATH = (
    _REPO_ROOT / "tests" / "bats" / "unit" / "build" / "test_verify_built_binary.bats"
)
_FAKE_CLAUDE_PATH = (
    _REPO_ROOT / "scripts" / "build" / "fixtures" / "fake-claude" / "claude"
)

# How the fake provider recognises a fix request: it looks for this fragment in
# the `--json-schema` argument lintro sends.
_SCHEMA_SNIFF = "'\"original_code\"' in schema"

# The risk level the fixture answers with, read back out of the fixture.
_FIXTURE_RISK_PATTERN = re.compile(r'"risk_level": "([a-z-]+)"')

# The command table the verify step runs once each, as written in the shell
# script itself.
_EXPORTED_COMMANDS_PATTERN = re.compile(
    r"^EXPORTED_COMMANDS=\(\n(.*?)\n\)$",
    re.MULTILINE | re.DOTALL,
)

# The literal the verify step waits for before stopping `lintro watch`.
_WATCH_MARKER_PATTERN = re.compile(r'^WATCH_READY_MARKER="([^"]+)"$', re.MULTILINE)

# Rich console markup tags, which the watcher's source carries but its output
# does not.
_RICH_MARKUP = re.compile(r"\[/?[a-z ]+\]")

# Scripted stand-in for `lintro mcp`: a stdio JSON-RPC server that answers
# `initialize` and `tools/list` and exits at EOF. `FAKE_MCP_MODE` selects a
# failure to stage; the other variables shape the healthy answers.
_FAKE_MCP_SERVER = """#!/usr/bin/env python3
import json
import os
import sys
import time

if sys.argv[1:2] != ["mcp"]:
    sys.exit(2)
mode = os.environ.get("FAKE_MCP_MODE", "healthy")
if mode == "crash":
    sys.stderr.write(
        "Traceback (most recent call last):\\n"
        "ModuleNotFoundError: No module named 'mcp.server.stdio'\\n"
    )
    sys.exit(1)
if mode == "silent":
    time.sleep(120)
tools = os.environ.get("FAKE_MCP_TOOLS", "lintro_ping").split(",")
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method not in ("initialize", "tools/list"):
        continue
    if mode == "malformed":
        sys.stdout.write("{not json\\n")
        sys.stdout.flush()
        break
    if mode == "error-" + method.split("/")[0]:
        reply = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "error": {"code": -32603, "message": "staged failure"},
        }
        sys.stdout.write(json.dumps(reply) + "\\n")
        sys.stdout.flush()
        continue
    if method == "initialize":
        result = {
            "protocolVersion": message["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": os.environ.get("FAKE_MCP_NAME", "lintro"),
                "version": os.environ.get("FAKE_MCP_VERSION", "0.0.0"),
            },
        }
    else:
        result = {
            "tools": [
                {"name": name, "inputSchema": {"type": "object"}}
                for name in tools
                if name
            ],
        }
    reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
    sys.stdout.write(json.dumps(reply) + "\\n")
    sys.stdout.flush()
if mode == "hang":
    time.sleep(120)
sys.exit(int(os.environ.get("FAKE_MCP_EXIT", "0")))
"""

# A pty is required for the send-loop tests; every CI platform has one, but
# the module must still import where it does not.
_HAS_PTY = hasattr(os, "fork") and sys.platform != "win32"

# Scripted stand-in for the binary: prints the review prompt twice, echoing
# each keypress it is sent, then exits cleanly.
_PROMPTING_CHILD = """#!/usr/bin/env python3
import os
import sys
import tty

tty.setcbreak(sys.stdin.fileno())
for _ in range(2):
    sys.stdout.write("  [y]accept group  [q]quit: ")
    sys.stdout.flush()
    key = os.read(0, 1).decode()
    sys.stdout.write("got:" + key + "\\n")
    sys.stdout.flush()
sys.exit(0)
"""

# Scripted stand-in that never prompts and never exits, so the session runs
# out of time and the child has to be killed.
_SILENT_CHILD = """#!/usr/bin/env python3
import time

time.sleep(120)
"""


def _write_child(path: Path, source: str) -> Path:
    """Write an executable stand-in for the built binary.

    Args:
        path: File to create.
        source: Python source for the stand-in.

    Returns:
        The path written, now executable.
    """
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


# A pygments-rendered diff captured from a real pty session of the driver.
_HIGHLIGHTED_DIFF = (
    b"\x1b[91m--- a/bad.py\x1b[0m\r\n\r\n\x1b[92m+++ b/bad.py\x1b[0m\r\n\r\n"
    b"\x1b[1;95m@@ -1 +1 @@\x1b[0m\r\n\r\n\x1b[91m-import os\x1b[0m\r\n"
    b"\x1b[92m+# import removed\x1b[0m\r\n"
)

# The same diff after a degraded render: pygments never resolved a lexer, so
# the markers arrive bare.
_PLAIN_DIFF = (
    b"--- a/bad.py\r\n+++ b/bad.py\r\n@@ -1 +1 @@\r\n"
    b"-import os\r\n+# import removed\r\n"
)

# Coloured output that is not a diff: lintro's own table rules and summary
# counts put SGR sequences immediately before `+` and `-` mid-line.
_COLOURED_NON_DIFF = (
    b"  \x1b[92m3 auto-fixable issues\x1b[0m\r\n"
    b"| \x1b[91m-1\x1b[0m | \x1b[92m+2\x1b[0m |\r\n"
    b"\x1b[2m+------+------+\x1b[0m\r\n"
)


def _load_module(path: Path, name: str) -> ModuleType:
    """Import a script as a module without running its entry point.

    Args:
        path: The script to import.
        name: Module name to register it under.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_driver() -> ModuleType:
    """Import the pty driver without running it.

    Returns:
        The loaded ``drive_interactive_review`` module.
    """
    return _load_module(_DRIVER_PATH, "drive_interactive_review")


def _load_mcp_driver() -> ModuleType:
    """Import the MCP round-trip driver without running it.

    Returns:
        The loaded ``drive_mcp_round_trip`` module.
    """
    return _load_module(_MCP_DRIVER_PATH, "drive_mcp_round_trip")


def _verify_exported_commands() -> list[str]:
    """Read the command table out of the verify script.

    Returns:
        The canonical command names the verify step runs once each.
    """
    source = _VERIFY_PATH.read_text(encoding="utf-8")
    match = _EXPORTED_COMMANDS_PATTERN.search(source)
    assert_that(match).is_not_none()
    assert match is not None  # narrow type for mypy
    return match.group(1).split()


def _verify_watch_marker() -> str:
    """Read the watch ready literal out of the verify script.

    Returns:
        The text the shell script waits for in ``lintro watch`` output.
    """
    source = _VERIFY_PATH.read_text(encoding="utf-8")
    match = _WATCH_MARKER_PATTERN.search(source)
    assert_that(match).is_not_none()
    assert match is not None  # narrow type for mypy
    assert_that(source).contains('grep -q "$WATCH_READY_MARKER"')
    return match.group(1)


def test_exported_commands_match_the_cli_command_table() -> None:
    """The verify step must run every command lintro exports, and only those.

    A command added to ``lintro.cli`` without an entry here would ship
    unexercised, which is how ``lintro mcp`` was broken in every release
    before #2577; a stale entry would fail every release on a usage error.
    """
    from lintro.cli import _COMMAND_MODULES

    assert_that(set(_verify_exported_commands())).is_equal_to(set(_COMMAND_MODULES))
    # The table must drive the run loop, or it is decorative.
    assert_that(_VERIFY_PATH.read_text(encoding="utf-8")).contains(
        'for name in "${EXPORTED_COMMANDS[@]}"; do',
    )


def test_every_exported_command_has_an_invocation() -> None:
    """Each command in the table needs its own ``command_argv`` case arm."""
    source = _VERIFY_PATH.read_text(encoding="utf-8")

    for name in _verify_exported_commands():
        assert_that(source).matches(rf"(?m)^\t{re.escape(name)}\) ")


def test_bats_stub_answers_every_exported_command() -> None:
    """The bats stub must answer every command the verify step runs.

    Otherwise a command added to the table is only ever run against a real
    binary on a release runner.
    """
    bats_source = _BATS_PATH.read_text(encoding="utf-8")

    for name in _verify_exported_commands():
        # A standalone or grouped case arm at line start, not a substring:
        # `test` occurs in every `@test` line and `config` in the fixture text.
        assert_that(bats_source).matches(
            rf"(?m)^(?:[^\n|)]*\|)*{re.escape(name)}(?:\|[^\n)]*)*\) ",
        )


def test_watch_marker_matches_the_watchers_ready_line() -> None:
    """The verify step waits for a line the watcher actually prints.

    The watcher's source carries rich markup that never reaches the terminal,
    so the pin is against the de-marked source.
    """
    watcher_source = _RICH_MARKUP.sub("", _WATCHER_PATH.read_text(encoding="utf-8"))

    assert_that(watcher_source).contains(_verify_watch_marker())


def test_mcp_driver_offers_the_latest_handshake_revision() -> None:
    """The driver's ``initialize`` offer is the newest revision the SDK negotiates.

    An older offer would be counter-offered and still pass; a revision the SDK
    no longer accepts would fail every release binary.
    """
    from mcp_types.version import LATEST_HANDSHAKE_VERSION

    mcp_driver = _load_mcp_driver()

    assert_that(mcp_driver.PROTOCOL_VERSION).is_equal_to(LATEST_HANDSHAKE_VERSION)


def test_mcp_driver_requires_a_tool_and_name_lintro_serves(tmp_path: Path) -> None:
    """The tool and server name the gate insists on are lintro's own.

    Args:
        tmp_path: Workspace root for the registry under test.
    """
    from lintro.mcp.server import build_default_registry, create_mcp_server

    mcp_driver = _load_mcp_driver()

    assert_that(mcp_driver.REQUIRED_TOOL).is_in(
        *[
            spec.name
            for spec in build_default_registry(workspace=tmp_path).list_tools()
        ],
    )
    assert_that(create_mcp_server(workspace=tmp_path).name).is_equal_to(
        mcp_driver.SERVER_NAME,
    )


@pytest.fixture
def fake_mcp_server(tmp_path: Path) -> Path:
    """Write the scripted stdio server stand-in.

    Args:
        tmp_path: Workspace for the stand-in.

    Returns:
        Path to the executable stand-in.
    """
    return _write_child(tmp_path / "fake-lintro", _FAKE_MCP_SERVER)


def test_mcp_drive_completes_the_round_trip(
    fake_mcp_server: Path,
    tmp_path: Path,
) -> None:
    """A server that answers both requests and exits at EOF passes the gate.

    Args:
        fake_mcp_server: The scripted stand-in.
        tmp_path: Workspace handed to the server.
    """
    mcp_driver = _load_mcp_driver()

    session = mcp_driver.drive(fake_mcp_server, tmp_path)

    assert_that(mcp_driver.session_failure(session)).is_empty()
    assert_that(session.server_info["name"]).is_equal_to("lintro")
    assert_that(session.tools).contains("lintro_ping")
    assert_that(session.exit_code).is_equal_to(0)
    assert_that(session.timed_out).is_false()


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"FAKE_MCP_MODE": "crash"}, "no response to initialize"),
        ({"FAKE_MCP_TOOLS": "lintro_check"}, "lintro_ping missing"),
        ({"FAKE_MCP_NAME": "other"}, "unexpected serverInfo"),
        ({"FAKE_MCP_VERSION": ""}, "serverInfo missing version"),
        ({"FAKE_MCP_EXIT": "3"}, "server exited 3"),
        ({"FAKE_MCP_MODE": "hang"}, "did not exit after stdin closed"),
        ({"FAKE_MCP_MODE": "silent"}, "no response to initialize"),
        ({"FAKE_MCP_MODE": "error-initialize"}, "initialize failed"),
        ({"FAKE_MCP_MODE": "error-tools"}, "tools/list failed"),
        ({"FAKE_MCP_MODE": "malformed"}, "no response to initialize"),
    ],
    ids=[
        "crash",
        "missing-tool",
        "wrong-name",
        "no-version",
        "exit-code",
        "hang",
        "silent",
        "error-on-initialize",
        "error-on-tools-list",
        "malformed-json",
    ],
)
def test_mcp_drive_fails_a_broken_server(
    fake_mcp_server: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    expected: str,
) -> None:
    """Every way the server can fall short is reported, none is accepted.

    The usage-error escape hatch is gone on purpose: a build that dropped the
    SDK and reported the documented usage error was accepted before #2577.

    Args:
        fake_mcp_server: The scripted stand-in.
        tmp_path: Workspace handed to the server.
        monkeypatch: Sets the stand-in's failure mode and shortens budgets.
        environment: Variables selecting the stand-in's behaviour.
        expected: Fragment of the failure the driver must report.
    """
    mcp_driver = _load_mcp_driver()
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(mcp_driver, "SESSION_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(mcp_driver, "REAP_GRACE_SECONDS", 1)

    session = mcp_driver.drive(fake_mcp_server, tmp_path)

    assert_that(mcp_driver.session_failure(session)).contains(expected)


def test_mcp_drive_reports_the_servers_stderr_on_a_crash(
    fake_mcp_server: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashed server's traceback reaches the log, not just "no response".

    Args:
        fake_mcp_server: The scripted stand-in.
        tmp_path: Workspace handed to the server.
        monkeypatch: Selects the crash mode.
    """
    mcp_driver = _load_mcp_driver()
    monkeypatch.setenv("FAKE_MCP_MODE", "crash")

    session = mcp_driver.drive(fake_mcp_server, tmp_path)

    assert_that(session.stderr).contains("No module named 'mcp.server.stdio'")
    assert_that(session.exit_code).is_equal_to(1)


def test_mcp_drive_lets_a_disappointing_server_exit_on_eof(
    fake_mcp_server: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy server that fails an assertion gets EOF, not the grace and a kill.

    Stdin closes before the child is reaped on every path, so the failure is
    reported within moments with the server's own exit code, never as a
    ``SIGKILL`` after ``REAP_GRACE_SECONDS``.

    Args:
        fake_mcp_server: The scripted stand-in.
        tmp_path: Workspace handed to the server.
        monkeypatch: Stages a tool list without ``lintro_ping``.
    """
    mcp_driver = _load_mcp_driver()
    monkeypatch.setenv("FAKE_MCP_TOOLS", "lintro_check")
    started = time.monotonic()

    session = mcp_driver.drive(fake_mcp_server, tmp_path)

    assert_that(time.monotonic() - started).is_less_than(
        mcp_driver.REAP_GRACE_SECONDS,
    )
    assert_that(session.failure).contains("lintro_ping missing")
    assert_that(session.exit_code).is_equal_to(0)
    assert_that(session.timed_out).is_false()


def test_mcp_main_reports_a_missing_binary(tmp_path: Path) -> None:
    """A missing binary fails the gate before any process is spawned.

    Args:
        tmp_path: Location for the missing path.
    """
    mcp_driver = _load_mcp_driver()

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["drive_mcp_round_trip.py", str(tmp_path / "x")])
        patcher.setattr(
            mcp_driver,
            "drive",
            lambda *args, **kwargs: pytest.fail("drive() must not run"),
        )
        exit_code = mcp_driver.main()

    assert_that(exit_code).is_equal_to(1)


def test_mcp_drive_round_trips_with_lintros_real_server(tmp_path: Path) -> None:
    """The driver speaks what lintro's actual stdio server understands.

    The stand-in above pins the driver's expectations; this pins them to the
    server itself, run from this interpreter the way the binary runs it.

    Args:
        tmp_path: Workspace for the wrapper and the server.
    """
    mcp_driver = _load_mcp_driver()
    wrapper = _write_child(
        tmp_path / "lintro",
        f'#!/bin/sh\nexec "{sys.executable}" -m lintro "$@"\n',
    )

    with pytest.MonkeyPatch.context() as patcher:
        # A test-local budget: the release values are sized for a cold onefile
        # extraction, not for a suite run.
        patcher.setattr(mcp_driver, "SESSION_TIMEOUT_SECONDS", 30)
        patcher.setattr(mcp_driver, "REAP_GRACE_SECONDS", 5)
        session = mcp_driver.drive(wrapper, tmp_path)

    assert_that(mcp_driver.session_failure(session)).is_empty()
    assert_that(session.tools).contains("lintro_ping")
    assert_that(session.protocol_version).is_equal_to(mcp_driver.PROTOCOL_VERSION)


def test_prompt_marker_matches_the_interactive_review_prompt() -> None:
    """The driver waits for a prompt fragment lintro actually prints.

    Both prompt forms are checked: the fixture's behavioural-risk fixes
    produce the non-safe-default form today, but a safe-style default must
    not silently strand the driver either.
    """
    from lintro.ai.interactive import _render_prompt

    driver = _load_driver()
    marker = driver.PROMPT_MARKER.decode()

    for safe_default in (False, True):
        prompt = _render_prompt(validate_mode=False, safe_default=safe_default)
        assert_that(prompt).contains(marker)


def test_review_keys_match_the_review_key_bindings() -> None:
    """The keys the driver sends must be lintro's own diff and quit bindings."""
    from lintro.ai.interactive import ReviewKey

    driver = _load_driver()

    assert_that(driver.REVIEW_KEYS).is_equal_to(
        (ReviewKey.SHOW_DIFF.value.encode(), ReviewKey.QUIT.value.encode()),
    )


@pytest.mark.parametrize(
    ("captured", "expected"),
    [
        (_HIGHLIGHTED_DIFF, True),
        (_PLAIN_DIFF, False),
        (_COLOURED_NON_DIFF, False),
        (_PLAIN_DIFF + _COLOURED_NON_DIFF, False),
    ],
    ids=["highlighted", "plain", "coloured-non-diff", "degraded-run"],
)
def test_diff_was_highlighted_only_accepts_coloured_diff_lines(
    captured: bytes,
    expected: bool,
) -> None:
    """Only a diff whose own +/- lines carry colour may pass the gate.

    Args:
        captured: Bytes standing in for a pty session's output.
        expected: Whether the driver should call that a pygments render.
    """
    driver = _load_driver()

    assert_that(driver.diff_was_highlighted(captured)).is_equal_to(expected)


def test_build_environment_is_hermetic_and_fixture_first() -> None:
    """The child must see the fixtures first and no inherited colour policy."""
    driver = _load_driver()

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setenv("NO_COLOR", "1")
        patcher.setenv("PATH", "/usr/bin")
        env = driver.build_environment()

    path_entries = env["PATH"].split(os.pathsep)
    assert_that(path_entries[0]).ends_with(os.path.join("fixtures", "fake-claude"))
    assert_that(path_entries[1]).ends_with(os.path.join("fixtures", "fake-ruff"))
    assert_that(path_entries).contains("/usr/bin")
    assert_that(env).does_not_contain_key("NO_COLOR")
    assert_that(env["LINTRO_GLOBAL_CONFIG"]).is_equal_to("off")
    assert_that(env["COLUMNS"]).is_equal_to("120")


def test_main_reports_a_missing_binary_instead_of_forking(tmp_path: Path) -> None:
    """A missing binary must fail the verify step before any pty is opened."""
    driver = _load_driver()
    missing = tmp_path / "lintro"

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["drive_interactive_review.py", str(missing)])
        patcher.setattr(
            driver,
            "drive",
            lambda *args, **kwargs: pytest.fail("drive() must not run"),
        )
        exit_code = driver.main()

    assert_that(exit_code).is_equal_to(1)


@pytest.mark.skipif(not _HAS_PTY, reason="requires a pty")
def test_drive_answers_each_prompt_once_in_order(tmp_path: Path) -> None:
    """The send loop must deliver ``d`` then ``q``, one key per prompt.

    Args:
        tmp_path: Workspace for the scripted child.
    """
    driver = _load_driver()
    binary = _write_child(tmp_path / "fake-lintro", _PROMPTING_CHILD)

    session = driver.drive(binary, tmp_path)

    assert_that(session.keys_sent).is_equal_to(len(driver.REVIEW_KEYS))
    assert_that(session.exit_code).is_equal_to(0)
    assert_that(session.timed_out).is_false()
    echoed = re.findall(rb"got:(.)", session.captured)
    assert_that(echoed).is_equal_to(list(driver.REVIEW_KEYS))


@pytest.mark.skipif(not _HAS_PTY, reason="requires a pty")
def test_drive_kills_a_child_that_never_prompts(tmp_path: Path) -> None:
    """A session that runs out of time is killed, flagged, and fails the gate.

    The flag matters on its own: a binary that rendered a diff and then hung
    would otherwise satisfy every other assertion.

    Args:
        tmp_path: Workspace for the scripted child.
    """
    driver = _load_driver()
    binary = _write_child(tmp_path / "fake-lintro", _SILENT_CHILD)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(driver, "SESSION_TIMEOUT_SECONDS", 1)
        patcher.setattr(driver, "REAP_GRACE_SECONDS", 1)
        session = driver.drive(binary, tmp_path)

        assert_that(session.keys_sent).is_equal_to(0)
        assert_that(session.captured).is_equal_to(b"")
        assert_that(session.exit_code).is_less_than(0)
        assert_that(session.timed_out).is_true()

        patcher.setattr(sys, "argv", ["drive_interactive_review.py", str(binary)])
        assert_that(driver.main()).is_equal_to(1)


@pytest.mark.skipif(not _HAS_PTY, reason="requires a pty")
def test_main_fails_a_timed_out_session_that_rendered_a_diff(
    tmp_path: Path,
) -> None:
    """A hung binary fails even when the diff and exit code look healthy.

    Args:
        tmp_path: Location for the stand-in binary path.
    """
    driver = _load_driver()
    binary = _write_child(tmp_path / "fake-lintro", _PROMPTING_CHILD)
    hung = driver.Session(
        captured=_HIGHLIGHTED_DIFF,
        keys_sent=len(driver.REVIEW_KEYS),
        exit_code=0,
        timed_out=True,
    )

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["drive_interactive_review.py", str(binary)])
        patcher.setattr(driver, "drive", lambda *args, **kwargs: hung)
        exit_code = driver.main()

    assert_that(exit_code).is_equal_to(1)


@pytest.mark.skipif(not _HAS_PTY, reason="requires a pty")
def test_main_fails_when_the_reviewed_binary_dies(tmp_path: Path) -> None:
    """An unexpected exit code fails the gate even with a rendered diff.

    Args:
        tmp_path: Location for the stand-in binary path.
    """
    driver = _load_driver()
    binary = _write_child(tmp_path / "fake-lintro", _PROMPTING_CHILD)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["drive_interactive_review.py", str(binary)])
        patcher.setattr(
            driver,
            "drive",
            lambda *args, **kwargs: driver.Session(
                captured=_HIGHLIGHTED_DIFF,
                keys_sent=len(driver.REVIEW_KEYS),
                exit_code=139,
                timed_out=False,
            ),
        )
        exit_code = driver.main()

    assert_that(exit_code).is_equal_to(1)


def test_accepted_exit_codes_cover_a_run_that_leaves_issues() -> None:
    """Quitting the review leaves issues, so ``check`` exits 1, not 0.

    Pinning both codes keeps a future tightening to ``0`` from turning every
    release into a false failure.
    """
    driver = _load_driver()

    assert_that(driver.ACCEPTED_EXIT_CODES).contains(0, 1)
    assert_that(driver.ACCEPTED_EXIT_CODES).does_not_contain(2)


def test_fake_provider_sniffs_a_field_lintro_actually_sends() -> None:
    """The fixture's schema test must match lintro's real fix schema.

    The fake provider decides between a fix array and a summary by looking for
    ``original_code`` in the ``--json-schema`` argument. If lintro renamed that
    field the fixture would answer every request with a summary, the review
    would never open, and the release gate would fail on the next tag.
    """
    from lintro.ai.cli_schemas import FIX_BATCH_CLI_SCHEMA

    fixture_source = _FAKE_CLAUDE_PATH.read_text(encoding="utf-8")
    assert_that(fixture_source).contains(_SCHEMA_SNIFF)

    assert_that(json.dumps(FIX_BATCH_CLI_SCHEMA)).contains('"original_code"')


def test_fake_provider_risk_level_is_one_lintro_accepts() -> None:
    """The fixture's risk level must be in lintro's enum, and behavioural.

    ``additionalProperties`` is false and the enum is closed, so an unknown
    value would be rejected; a *safe-style* value would route the run to the
    auto-apply fast path and the interactive review would never render a diff.
    """
    from lintro.ai.cli_schemas import FIX_BATCH_CLI_SCHEMA

    fixture_source = _FAKE_CLAUDE_PATH.read_text(encoding="utf-8")
    risk_levels = set(_FIXTURE_RISK_PATTERN.findall(fixture_source))
    assert_that(risk_levels).is_length(1)

    schema_properties = FIX_BATCH_CLI_SCHEMA["properties"]
    assert isinstance(schema_properties, dict)  # narrow type for mypy
    fixes = schema_properties["fixes"]
    assert isinstance(fixes, dict)  # narrow type for mypy
    items = fixes["items"]
    assert isinstance(items, dict)  # narrow type for mypy
    properties = items["properties"]
    assert isinstance(properties, dict)  # narrow type for mypy
    risk_level = properties["risk_level"]
    assert isinstance(risk_level, dict)  # narrow type for mypy

    assert_that(risk_level["enum"]).contains(*risk_levels)
    assert_that(risk_levels).contains("behavioral-risk")
