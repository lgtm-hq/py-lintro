#!/usr/bin/env python3
"""Smoke-test a built lintro binary against the runtime tool registry.

Release binaries (npm, Homebrew) are Nuitka onefile builds. They can pass a
``--version``/``--help`` check while still shipping an empty tool registry,
which is exactly how #2006 reached users: every registry-backed command
reported ``No tools to run.``.

This script exercises the registry through the binary itself:

1. ``lintro list-tools --json`` reports **every** builtin the generated index
   says exists — a partially populated registry fails just like an empty one.
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
import ast
import json
import re
import subprocess  # nosec B404 - driving the built CLI is the point of this script; all invocations use shell=False
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_FAILED = 1

REPO_ROOT = Path(__file__).resolve().parents[2]

# Generated index of builtin definition modules; its registering subset is the
# expected builtin tool set (module names match tool names modulo separator).
BUILTIN_INDEX_PATH = REPO_ROOT / "lintro" / "plugins" / "_builtin_index.py"
REGISTERING_MODULES_NAME = "REGISTERING_TOOL_MODULES"

# Generous but bounded: a cold onefile binary pays an extraction cost on the
# first run, and ``check`` fans out across every registered tool.
LIST_TIMEOUT_SECONDS = 120
CHECK_TIMEOUT_SECONDS = 300

# Emitted by ``lintro/utils/tool_executor.py`` when the registry is empty. It
# is a fast, readable signal rather than the guard of record: the positive
# tool-execution evidence below is what fails closed if this wording changes.
EMPTY_REGISTRY_MARKER = "No tools to run."

# Statuses rendered in the per-tool result table. Their presence in a table row
# is what distinguishes a real result row from prose that names a tool.
RESULT_ROW_STATUSES = ("PASS", "FAIL", "SKIP")


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


def expected_builtin_tools(index_path: Path) -> list[str]:
    """Read the builtin tool names the generated index says should exist.

    Parsed with :mod:`ast` rather than imported so the script stays stdlib-only
    and never pulls the ``lintro`` package into the release runner's process.

    Args:
        index_path: Path to the generated ``_builtin_index.py``.

    Returns:
        Sorted expected tool names, or an empty list when the index cannot be
        read or does not declare the registering-module tuple.
    """
    try:
        tree = ast.parse(index_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        print(f"WARN: could not read {index_path}: {exc}", file=sys.stderr)
        return []

    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        else:
            continue
        if target != REGISTERING_MODULES_NAME or value is None:
            continue
        try:
            return sorted(str(name) for name in ast.literal_eval(value))
        except ValueError:
            break

    print(
        f"WARN: {index_path} declares no {REGISTERING_MODULES_NAME}",
        file=sys.stderr,
    )
    return []


def list_builtin_tools(binary: Path, expected: list[str]) -> list[str] | None:
    """Read the builtin tool names ``list-tools --json`` reports.

    Args:
        binary: Path to the lintro binary under test.
        expected: Builtin tool names the generated index says should exist. An
            empty list only checks that some builtin is present.

    Returns:
        The builtin tool names, or ``None`` when the command failed, reported no
        builtins (the #2006 symptom), or dropped tools the index expects.
        Failures are printed as they occur.
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

    # A partially populated registry is the same class of bug as an empty one:
    # a build that drops most definition modules would otherwise pass.
    reported = {name.replace("-", "_") for name in builtins}
    missing = [name for name in expected if name.replace("-", "_") not in reported]
    if missing:
        _fail(
            f"list-tools --json is missing {len(missing)} of {len(expected)} "
            f"builtin tools the index expects: {missing}",
        )
        return None

    print(
        f"OK: list-tools reports {len(builtins)} builtin tools "
        f"({len(expected)} expected by the index)",
    )
    return builtins


_TOOL_TOKEN = re.compile(r"[A-Za-z0-9_-]+")


def _tool_spellings(name: str) -> set[str]:
    """Return the identifier spellings a tool name may appear as.

    Args:
        name: Tool name as the registry spells it.

    Returns:
        The original name plus underscore/hyphen variants.
    """
    return {name, name.replace("_", "-"), name.replace("-", "_")}


def _matches_tool(*, text: str, name: str) -> bool:
    """Check whether a complete tool identifier appears in text.

    Report and config output normalize underscores to hyphens (``pip_audit``
    renders as ``pip-audit``), so both spellings count as the same tool.
    Matching is token-based so an external ``ruff-format`` entry cannot
    satisfy builtin ``ruff``.

    Args:
        text: Text to search.
        name: Tool name as the registry spells it.

    Returns:
        True when the tool is named in the text as a complete identifier.
    """
    return bool(set(_TOOL_TOKEN.findall(text)) & _tool_spellings(name))


def check_config(binary: Path, builtin_tools: list[str]) -> int:
    """Assert ``config --json`` reports builtin tools.

    Args:
        binary: Path to the lintro binary under test.
        builtin_tools: Builtin tool names reported by ``list-tools --json``.

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

    # A non-empty order built purely from third-party plugins would still mean
    # the builtin registry is empty, so require a builtin in it.
    ordered_names = [
        str(entry.get("tool", "")) if isinstance(entry, dict) else str(entry)
        for entry in order
    ]
    builtins_in_order = [
        name
        for name in builtin_tools
        if any(_matches_tool(text=ordered, name=name) for ordered in ordered_names)
    ]
    if not builtins_in_order:
        return _fail(
            "config --json reported a tool execution order without any builtin "
            f"tool: {ordered_names}",
        )

    print(
        f"OK: config reports {len(order)} tools in the execution order "
        f"({len(builtins_in_order)} builtin)",
    )
    return EXIT_OK


def _result_table_tool_cell(row: str) -> str:
    """Return the Tool column of a tabulate grid row.

    Matching the complete row would let a Notes cell mentioning ``ruff.py``
    satisfy builtin ``ruff``. Only the first data cell is the tool identity.

    Args:
        row: A stripped table row starting with ``|``.

    Returns:
        The first data cell, or the whole row when the shape is unexpected.
    """
    data_cells = [cell.strip() for cell in row.split("|") if cell.strip()]
    return data_cells[0] if data_cells else row


def _tools_in_result_table(*, output: str, builtin_tools: list[str]) -> list[str]:
    """Find builtin tools that produced a per-tool result row.

    Only rows of the rendered result table count: a row exists exactly when the
    registry handed the tool to the executor, which is the property #2006 broke.
    Prose lines that merely mention a tool (``Skipping ruff: executable not
    found``) are not evidence and are ignored.

    Rows with a ``SKIP`` status still count. A release runner has none of the
    external tool binaries installed, so every row there is a skip — yet the
    rows themselves prove the registry was populated and dispatched.

    Args:
        output: Combined stdout and stderr of the ``check`` run.
        builtin_tools: Builtin tool names reported by ``list-tools --json``.

    Returns:
        Sorted names of builtin tools that have a result row.
    """
    found: set[str] = set()
    for line in output.splitlines():
        row = line.strip()
        if not row.startswith("|"):
            continue
        if not any(status in row for status in RESULT_ROW_STATUSES):
            continue
        tool_cell = _result_table_tool_cell(row)
        found.update(
            name
            for name in builtin_tools
            if _matches_tool(text=tool_cell, name=name)
        )
    return sorted(found)


def check_reaches_execution(binary: Path, builtin_tools: list[str]) -> int:
    """Assert ``check`` runs tools instead of reporting an empty registry.

    The verdict is driven by positive evidence — the run must emit a per-tool
    result row for at least one builtin the registry advertised — so a binary
    that dies before tool execution fails even if it never prints the
    empty-registry marker or a traceback.

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

    reported = _tools_in_result_table(output=output, builtin_tools=builtin_tools)
    if not reported:
        return _fail(
            "check produced no per-tool result rows for any builtin tool "
            f"(looked for {len(builtin_tools)} registry names):\n{output[-2000:]}",
        )

    print(
        f"OK: check reached tool execution (exit {result.returncode}, "
        f"{len(reported)} builtin tools in the result table)",
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
        builtin_tools = list_builtin_tools(
            binary,
            expected_builtin_tools(BUILTIN_INDEX_PATH),
        )
        results = [
            EXIT_FAILED if builtin_tools is None else EXIT_OK,
            check_config(binary, builtin_tools or []),
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
