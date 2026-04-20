#!/usr/bin/env python3
"""Verify installed tools against the manifest inside a container image."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from typing import Any

_VERSION_RE = re.compile(r"\d+(?:\.\d+){1,3}")


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return 127, ""
    except PermissionError as exc:
        return 126, f"permission denied: {exc}"
    except OSError as exc:
        return 125, f"OS error running command: {exc}"
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        )
        output = stdout + stderr
        if not output:
            output = "Command timed out"
        return 124, output.strip()
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def _parse_version(output: str, tool_name: str) -> str | None:
    if tool_name == "clippy":
        match = re.search(r"clippy\s+0\.1\.(\d+)", output, re.IGNORECASE)
        if match:
            return f"1.{match.group(1)}.0"
    match = _VERSION_RE.search(output)
    if not match:
        return None
    return match.group(0)


def _tool_command(
    tool_name: str,
    tool_entry: dict[str, Any],
) -> list[str]:
    version_command = tool_entry.get("version_command")
    if not isinstance(version_command, list) or not version_command:
        raise ValueError(
            f"tool {tool_name!r} requires a non-empty 'version_command' list, "
            f"got {version_command!r}",
        )
    bad = [t for t in version_command if not isinstance(t, str) or not t.strip()]
    if bad:
        raise ValueError(
            f"tool {tool_name!r} has invalid version_command tokens: {bad!r}",
        )
    return version_command


def _load_manifest(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a JSON object, got {type(data).__name__}")
    raw_version = data.get("version")
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise ValueError(f"manifest version must be an integer, got {raw_version!r}")
    if raw_version != 2:
        raise ValueError(
            f"unsupported manifest version {raw_version}, required: 2",
        )
    tools = data.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("manifest tools must be a list")
    for i, entry in enumerate(tools):
        if not isinstance(entry, dict):
            raise ValueError(
                f"manifest tools[{i}] must be a dict, got {type(entry).__name__}",
            )
    return tools


def _iter_tools(
    tools: list[dict[str, Any]],
    tiers: Iterable[str],
) -> list[dict[str, Any]]:
    allowed = {t.strip() for t in tiers if t.strip()}
    selected = []
    for tool in tools:
        tier = tool.get("tier", "tools")
        if tier in allowed:
            selected.append(tool)
    return selected


def main() -> int:
    """Verify tools in manifest.json are installed with correct versions."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default=os.environ.get("LINTRO_MANIFEST", "lintro/tools/manifest.json"),
        help="Path to manifest.json",
    )
    parser.add_argument(
        "--tiers",
        default=os.environ.get("LINTRO_MANIFEST_TIERS", "tools"),
        help="Comma-separated tiers to verify (default: tools)",
    )
    args = parser.parse_args()

    tiers = [t.strip() for t in args.tiers.split(",")]
    try:
        all_tools = _load_manifest(args.manifest)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Failed to load manifest {args.manifest}: {exc}", file=sys.stderr)
        return 2
    tools = _iter_tools(all_tools, tiers)

    if not tools:
        print(f"No tools found for tiers {tiers} in {args.manifest}")
        return 2

    failures: list[str] = []
    for tool in tools:
        name = str(tool.get("name", "")).strip()
        expected = str(tool.get("version", "")).strip()
        if not name or not expected:
            failures.append(f"{name or '<unknown>'}: missing name or version")
            continue

        try:
            cmd = _tool_command(name, tool)
        except ValueError as exc:
            failures.append(f"{name}: invalid manifest entry ({exc})")
            continue
        code, output = _run(cmd)
        if code != 0:
            cmd_str = " ".join(cmd)
            diagnostic = output.strip()
            message = f"{name}: command failed with exit code {code} ({cmd_str})"
            if diagnostic:
                message = f"{message}: {diagnostic}"
            failures.append(message)
            continue

        actual = _parse_version(output, name)
        if not actual:
            failures.append(f"{name}: failed to parse version from '{output}'")
            continue

        if actual != expected:
            failures.append(
                f"{name}: version mismatch (expected {expected}, got {actual})",
            )

    if failures:
        print("Tool verification failed:")
        for item in failures:
            print(f"  - {item}")
        return 1

    print(f"Verified {len(tools)} tool(s) against manifest tiers: {', '.join(tiers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
