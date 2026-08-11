#!/usr/bin/env python3
"""Smoke-test a built lintro binary against the runtime tool registry.

Release binaries (npm, Homebrew) are Nuitka onefile builds. They can pass a
``--version``/``--help`` check while still shipping an empty tool registry,
which is exactly how #2006 reached users: every registry-backed command
reported ``No tools to run.``.

This script exercises the registry through the binary itself:

1. ``lintro list-tools --json`` returns a non-empty set containing builtins.
2. ``lintro config --json`` reports builtin tools in the execution order.
3. ``lintro check`` on a throwaway sample tree reaches tool execution instead
   of bailing out with ``No tools to run.``.

Usage:
    python3 scripts/ci/smoke-test-binary.py dist/nuitka/lintro

Stdlib-only on purpose: it runs on release runners that never install lintro's
dev dependencies.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - driving the built CLI is the point of this script; all invocations use shell=False
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_FAILED = 1

# Generous but bounded: a cold onefile binary pays an extraction cost on the
# first run, and ``check`` fans out across every registered tool.
LIST_TIMEOUT_SECONDS = 120
CHECK_TIMEOUT_SECONDS = 300

# Emitted by ``lintro/utils/tool_executor.py`` when the registry is empty. It
# is a fast, readable signal rather than the guard of record: the positive
# tool-execution evidence below is what fails closed if this wording changes.
EMPTY_REGISTRY_MARKER = "No tools to run."


def _run(
    *,
    argv: list[str],
    cwd: Path | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a binary invocation and capture its output.

    Args:
        argv: Full argument vector to execute.
        cwd: Working directory for the invocation.
        timeout: Seconds to wait before giving up.

    Returns:
        The completed process, including captured stdout and stderr.
    """
    return subprocess.run(  # nosec B603 - argv is built internally and run with shell=False; no user shell input
        argv,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _fail(message: str) -> int:
    """Report a smoke-test failure.

    Args:
        message: Human-readable failure description.

    Returns:
        The failure exit code, for use in ``return _fail(...)``.
    """
    print(f"FAIL: {message}", file=sys.stderr)
    return EXIT_FAILED


def list_builtin_tools(binary: Path) -> list[str] | None:
    """Read the builtin tool names ``list-tools --json`` reports.

    Args:
        binary: Path to the lintro binary under test.

    Returns:
        The builtin tool names, or ``None`` when the command failed or reported
        no builtins (the #2006 symptom). Failures are printed as they occur.
    """
    result = _run(
        argv=[str(binary), "list-tools", "--json"],
        timeout=LIST_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        _fail(f"list-tools --json exited {result.returncode}: {result.stderr.strip()}")
        return None

    try:
        tools = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        _fail(f"list-tools --json emitted invalid JSON: {exc}")
        return None

    if not isinstance(tools, dict) or not tools:
        _fail("list-tools --json reported an empty tool registry")
        return None

    builtins = [
        name
        for name, meta in tools.items()
        if isinstance(meta, dict) and meta.get("origin") == "builtin"
    ]
    if not builtins:
        _fail(f"list-tools --json reported no builtin tools: {sorted(tools)}")
        return None

    print(f"OK: list-tools reports {len(builtins)} builtin tools")
    return builtins


def check_config(binary: Path) -> int:
    """Assert ``config --json`` reports builtin tools.

    Args:
        binary: Path to the lintro binary under test.

    Returns:
        ``0`` on success, ``1`` on failure.
    """
    result = _run(
        argv=[str(binary), "config", "--json"],
        timeout=LIST_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return _fail(
            f"config --json exited {result.returncode}: {result.stderr.strip()}",
        )

    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return _fail(f"config --json emitted invalid JSON: {exc}")

    if not isinstance(config, dict):
        return _fail("config --json did not emit a JSON object")

    order = config.get("tool_execution_order")
    if not isinstance(order, list) or not order:
        return _fail("config --json reported an empty tool execution order")

    print(f"OK: config reports {len(order)} tools in the execution order")
    return EXIT_OK


def check_reaches_execution(binary: Path, builtin_tools: list[str]) -> int:
    """Assert ``check`` runs tools instead of reporting an empty registry.

    The verdict is driven by positive evidence — the run must name at least one
    of the builtin tools the registry advertised — so a binary that dies before
    tool execution fails even if it never prints the empty-registry marker or a
    traceback.

    Args:
        binary: Path to the lintro binary under test.
        builtin_tools: Builtin tool names reported by ``list-tools --json``.

    Returns:
        ``0`` on success, ``1`` on failure.
    """
    with tempfile.TemporaryDirectory() as tmp:
        sample_root = Path(tmp)
        (sample_root / "sample.py").write_text("x = 1\n")
        (sample_root / "sample.yaml").write_text("key: value\n")

        result = _run(
            argv=[str(binary), "check", "."],
            cwd=sample_root,
            timeout=CHECK_TIMEOUT_SECONDS,
        )

    output = f"{result.stdout}\n{result.stderr}"

    if EMPTY_REGISTRY_MARKER in output:
        return _fail(
            f"check reported {EMPTY_REGISTRY_MARKER!r}: the tool registry is empty",
        )
    if "Traceback (most recent call last)" in output:
        return _fail(f"check crashed:\n{output[-2000:]}")
    # 0 = clean, 1 = issues found. Anything else means the run never got to a
    # verdict (config error, crash, missing registry).
    if result.returncode not in (0, 1):
        return _fail(
            f"check exited {result.returncode} without a verdict:\n{output[-2000:]}",
        )

    # Positive evidence: the per-tool result table names the tools that ran.
    # Tool names are normalized in the report (e.g. ``pip_audit`` renders as
    # ``pip-audit``), so both spellings count.
    reported = [
        name
        for name in builtin_tools
        if name in output or name.replace("_", "-") in output
    ]
    if not reported:
        return _fail(
            "check produced no evidence that any builtin tool ran "
            f"(looked for {len(builtin_tools)} registry names):\n{output[-2000:]}",
        )

    print(
        f"OK: check reached tool execution (exit {result.returncode}, "
        f"{len(reported)} builtin tools named in the report)",
    )
    return EXIT_OK


def main() -> int:
    """Run every binary smoke test.

    Returns:
        ``0`` when all checks pass, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Smoke-test a built lintro binary's tool registry.",
    )
    parser.add_argument(
        "binary",
        type=Path,
        help="Path to the built lintro binary.",
    )
    args = parser.parse_args()

    binary: Path = args.binary.resolve()
    if not binary.is_file():
        return _fail(f"binary not found: {binary}")

    print(f"Smoke-testing {binary}")

    try:
        builtin_tools = list_builtin_tools(binary)
        results = [
            EXIT_FAILED if builtin_tools is None else EXIT_OK,
            check_config(binary),
            check_reaches_execution(binary, builtin_tools or []),
        ]
    except subprocess.TimeoutExpired as exc:
        return _fail(f"timed out running {exc.cmd}")

    if any(results):
        return EXIT_FAILED

    print("All binary smoke tests passed")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
