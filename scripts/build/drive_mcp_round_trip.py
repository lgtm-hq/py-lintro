#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Complete one MCP round trip over stdio against a built lintro binary.

The release verify step runs this against ``dist/nuitka/lintro`` (#2577).
It starts ``lintro mcp`` as a subprocess, speaks newline-delimited JSON-RPC
on its stdin/stdout, and requires the two requests every MCP client sends
first to succeed: ``initialize`` must answer with lintro's server info, and
``tools/list`` must return the built-in ``lintro_ping`` tool. The binary
then has to exit cleanly once stdin closes.

Stdlib only: the runner has no lintro environment, only ``python3``.

Usage:
    python3 scripts/build/drive_mcp_round_trip.py <binary-path>

Exit status is 0 when the round trip completed, 1 otherwise. The diagnosis
(what was expected, what came back, the server's stderr) goes to stdout so
it lands in the workflow log.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess  # nosec B404 - the binary under test is spawned with shell=False
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO, Any, NamedTuple

#: The protocol revision the driver offers in ``initialize``: the newest one
#: reachable through the handshake (the 2026 era has no ``initialize``).
#: Pinned to the SDK's ``LATEST_HANDSHAKE_VERSION`` by
#: tests/scripts/test_release_gate_contracts.py.
PROTOCOL_VERSION = "2025-11-25"

#: The built-in tool every lintro server registers first; its absence means
#: the registry did not load inside the binary.
REQUIRED_TOOL = "lintro_ping"

#: Server name lintro announces in the ``initialize`` result.
SERVER_NAME = "lintro"

#: Whole-session budget, including the cold onefile extraction.
SESSION_TIMEOUT_SECONDS = 120

#: How long the server gets to exit after stdin closes before it is killed.
REAP_GRACE_SECONDS = 10

#: Markers on stderr that mean the server crashed rather than declined.
CRASH_MARKERS = ("Traceback", "No module named")


class Session(NamedTuple):
    """Outcome of one driven MCP session.

    Attributes:
        server_info: The ``serverInfo`` object from the ``initialize`` result.
        protocol_version: The revision the server negotiated in ``initialize``.
        tools: Tool names the ``tools/list`` result carried.
        tool_list_ttl_ms: The ``ttlMs`` freshness hint on the tool list, when
            the negotiated revision carries one (handshake-era wires do not).
        exit_code: The child's exit code, negative when a signal ended it.
        stderr: Everything the child wrote to stderr.
        timed_out: Whether any wait in the session ran out of budget.
        failure: Human-readable reason the session failed, empty on success.
    """

    server_info: dict[str, Any]
    protocol_version: str
    tools: list[str]
    tool_list_ttl_ms: int | None
    exit_code: int
    stderr: str
    timed_out: bool
    failure: str


def initialize_request(*, request_id: int) -> dict[str, Any]:
    """Build the ``initialize`` request.

    Args:
        request_id: JSON-RPC id for the request.

    Returns:
        The JSON-RPC request object.
    """
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "lintro-release-gate", "version": "1"},
        },
    }


def initialized_notification() -> dict[str, Any]:
    """Build the ``notifications/initialized`` notification.

    Returns:
        The JSON-RPC notification object.
    """
    return {"jsonrpc": "2.0", "method": "notifications/initialized"}


def tools_list_request(*, request_id: int) -> dict[str, Any]:
    """Build the ``tools/list`` request.

    Args:
        request_id: JSON-RPC id for the request.

    Returns:
        The JSON-RPC request object.
    """
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/list"}


def _pump(stream: IO[bytes], sink: queue.Queue[bytes | None]) -> None:
    """Forward lines from ``stream`` to ``sink`` until EOF.

    Args:
        stream: The child's stdout.
        sink: Queue receiving each line; ``None`` marks EOF.
    """
    try:
        for line in iter(stream.readline, b""):
            sink.put(line)
    finally:
        sink.put(None)


def read_response(
    *,
    lines: queue.Queue[bytes | None],
    request_id: int,
    deadline: float,
) -> dict[str, Any] | None:
    """Read messages until the response to ``request_id`` arrives.

    Notifications and responses to other ids are skipped; malformed lines are
    skipped too, so a stray log line on stdout cannot mask a valid reply.

    Args:
        lines: Queue fed by :func:`_pump`.
        request_id: The id whose response is awaited.
        deadline: ``time.monotonic()`` value after which the wait fails.

    Returns:
        The JSON-RPC response object, or ``None`` on EOF or timeout.
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message


def _write(stream: IO[bytes], message: dict[str, Any]) -> None:
    """Send one newline-delimited JSON-RPC message.

    Args:
        stream: The child's stdin.
        message: The message to send.
    """
    stream.write(json.dumps(message).encode("utf-8") + b"\n")
    stream.flush()


def _reap(child: subprocess.Popen[bytes], *, grace: float) -> tuple[int, bool]:
    """Wait for the child to exit, killing it when the grace period lapses.

    Args:
        child: The server process.
        grace: Seconds to wait before killing.

    Returns:
        The exit code and whether the child had to be killed.
    """
    try:
        return child.wait(timeout=grace), False
    except subprocess.TimeoutExpired:
        child.kill()
        return child.wait(), True


def drive(binary: Path, workspace: Path) -> Session:
    """Run the ``initialize`` + ``tools/list`` round trip against ``binary``.

    Args:
        binary: Path to the built lintro binary.
        workspace: Directory to hand the server as its workspace root.

    Returns:
        The session outcome; ``failure`` is empty when everything passed.
    """
    env = dict(os.environ)
    # Hermetic: a developer's ~/.lintro-config.yaml must not reach this run.
    env["LINTRO_GLOBAL_CONFIG"] = "off"
    child = subprocess.Popen(  # nosec B603 - fixed argv, shell=False
        [str(binary), "mcp", "--workspace", str(workspace)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=workspace,
        env=env,
    )
    assert child.stdin is not None and child.stdout is not None  # for mypy
    assert child.stderr is not None  # for mypy

    lines: queue.Queue[bytes | None] = queue.Queue()
    threading.Thread(target=_pump, args=(child.stdout, lines), daemon=True).start()
    stderr_chunks: list[bytes] = []
    stderr_thread = threading.Thread(
        target=lambda: stderr_chunks.append(child.stderr.read()),  # type: ignore[union-attr]
        daemon=True,
    )
    stderr_thread.start()

    deadline = time.monotonic() + SESSION_TIMEOUT_SECONDS
    server_info: dict[str, Any] = {}
    protocol_version = ""
    tools: list[str] = []
    ttl_ms: int | None = None
    timed_out = False

    def finish(reason: str) -> Session:
        nonlocal timed_out
        # EOF first, on every path: a server that answered but disappointed is
        # still healthy and exits on its own once stdin closes; reaping before
        # that would burn the grace period and report a SIGKILL exit instead.
        try:
            child.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        exit_code, killed = _reap(child, grace=REAP_GRACE_SECONDS)
        timed_out = timed_out or killed
        stderr_thread.join(timeout=REAP_GRACE_SECONDS)
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        return Session(
            server_info=server_info,
            protocol_version=protocol_version,
            tools=tools,
            tool_list_ttl_ms=ttl_ms,
            exit_code=exit_code,
            stderr=stderr,
            timed_out=timed_out,
            failure=reason,
        )

    try:
        _write(child.stdin, initialize_request(request_id=1))
        response = read_response(lines=lines, request_id=1, deadline=deadline)
        if response is None:
            timed_out = time.monotonic() >= deadline
            return finish("no response to initialize")
        result = response.get("result")
        if not isinstance(result, dict):
            return finish(f"initialize failed: {json.dumps(response)}")
        server_info = dict(result.get("serverInfo") or {})
        protocol_version = str(result.get("protocolVersion") or "")
        if server_info.get("name") != SERVER_NAME:
            return finish(f"unexpected serverInfo: {json.dumps(server_info)}")
        if not server_info.get("version"):
            return finish(f"serverInfo missing version: {json.dumps(server_info)}")

        _write(child.stdin, initialized_notification())
        _write(child.stdin, tools_list_request(request_id=2))
        response = read_response(lines=lines, request_id=2, deadline=deadline)
        if response is None:
            timed_out = time.monotonic() >= deadline
            return finish("no response to tools/list")
        result = response.get("result")
        if not isinstance(result, dict):
            return finish(f"tools/list failed: {json.dumps(response)}")
        tools = [
            str(tool.get("name"))
            for tool in result.get("tools") or []
            if isinstance(tool, dict)
        ]
        ttl = result.get("ttlMs")
        ttl_ms = ttl if isinstance(ttl, int) else None
        if REQUIRED_TOOL not in tools:
            return finish(f"{REQUIRED_TOOL} missing from tools/list: {tools}")
    except BrokenPipeError:
        return finish("server closed its stdin pipe early")

    return finish("")


def session_failure(session: Session) -> str:
    """Classify a completed session.

    Args:
        session: The outcome of :func:`drive`.

    Returns:
        Why the gate fails, or an empty string when the session passes.
    """
    if session.failure:
        return session.failure
    if session.timed_out:
        return "server did not exit after stdin closed"
    if session.exit_code != 0:
        return f"server exited {session.exit_code} after the round trip"
    for marker in CRASH_MARKERS:
        if marker in session.stderr:
            return f"server stderr contains {marker!r}"
    return ""


def main() -> int:
    """Run the round trip and report.

    Returns:
        ``0`` when the gate passes, ``1`` otherwise.
    """
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <binary-path>")
        return 1
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        print(f"FAIL mcp round trip: binary not found: {binary}")
        return 1

    with tempfile.TemporaryDirectory(prefix="lintro-mcp-gate-") as tmp:
        session = drive(binary, Path(tmp))

    reason = session_failure(session)
    if reason:
        print(f"FAIL mcp round trip: {reason}")
        print(f"  serverInfo: {json.dumps(session.server_info)}")
        print(f"  tools: {session.tools}")
        print(f"  exit code: {session.exit_code}")
        if session.stderr.strip():
            print("  server stderr:")
            for line in session.stderr.rstrip().splitlines():
                print(f"    {line}")
        return 1

    print(
        f"OK mcp round trip: {SERVER_NAME} {session.server_info['version']} "
        f"(protocol {session.protocol_version}) listed {len(session.tools)} "
        f"tools (ttlMs={session.tool_list_ttl_ms}) and exited cleanly",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
