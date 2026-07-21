#!/usr/bin/env python3
"""Verify installed tools against the manifest inside a container image."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False
import sys
from collections.abc import Iterable
from typing import Any

_VERSION_RE = re.compile(r"\d+(?:\.\d+){1,3}")


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
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


def _versions_match(tool_name: str, expected: str, actual: str) -> bool:
    # `cargo clippy --version` reports only `clippy 0.1.<minor>`; it never
    # exposes the toolchain patch level, so `_parse_version` synthesizes a
    # trailing `.0`. Comparing that against a manifest patch (e.g. 1.97.1)
    # would always fail. Match clippy at major.minor granularity — the patch
    # is unobservable from the binary, while any real minor/major drift is
    # still caught.
    if tool_name == "clippy":
        # Compare only over the segments the manifest actually declares (still
        # capped at major.minor), so a major-only manifest version like "1"
        # matches "1.97.0" instead of erroring on a missing minor segment.
        expected_parts = expected.split(".")[:2]
        actual_parts = actual.split(".")[: len(expected_parts)]
        return actual_parts == expected_parts
    return expected == actual


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


# Exit code returned by `_run` when the binary itself cannot be found on PATH
# (FileNotFoundError). This is the ONLY failure mode tolerated for an
# allow-missing tool: the tool the PR introduces is not yet baked into the
# digest-pinned base image, so its binary is simply absent. Any other non-zero
# exit means the binary IS present but misbehaving, which stays a hard failure
# even for an allow-missing tool.
_MISSING_BINARY_EXIT = 127


def _parse_allow_missing(values: list[str] | None) -> set[str]:
    """Parse repeated/comma-separated --allow-missing values into a name set.

    Args:
        values: Raw ``--allow-missing`` argument values, each of which may
            itself be a comma-separated list of tool names. ``None`` when the
            flag was never supplied.

    Returns:
        The set of tool names whose missing binary should be tolerated.
    """
    if not values:
        return set()
    names: set[str] = set()
    for value in values:
        names.update(part.strip() for part in value.split(",") if part.strip())
    return names


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
    parser.add_argument(
        "--allow-missing",
        action="append",
        default=None,
        help=(
            "Tool name(s) whose missing binary downgrades to a warning instead "
            "of failing. Repeatable and/or comma-separated. Intended for the "
            "tool a PR introduces, which is not yet in the digest-pinned base "
            "image. An allow-missing tool that IS present must still "
            "version-match; every other tool keeps hard-fail behavior."
        ),
    )
    args = parser.parse_args()

    tiers = [t.strip() for t in args.tiers.split(",")]
    allow_missing = _parse_allow_missing(args.allow_missing)
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
    warnings: list[str] = []
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
            # Tolerate ONLY a genuinely-absent binary (127) for a tool the PR
            # introduces: the digest-pinned base image cannot yet contain it,
            # so downgrade to a loud warning instead of a hard failure. The
            # post-merge tools-image republish + digest bump restores full
            # coverage. Any other exit code means the binary is present but
            # broken, which stays a failure even for an allow-missing tool.
            if name in allow_missing and code == _MISSING_BINARY_EXIT:
                warnings.append(
                    f"{name}: binary not found in image ({cmd_str}); tolerated "
                    f"because this tool is newly added by the PR and is not yet "
                    f"in the digest-pinned base image",
                )
                continue
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

        if not _versions_match(name, expected, actual):
            failures.append(
                f"{name}: version mismatch (expected {expected}, got {actual})",
            )

    if warnings:
        # GitHub Actions annotation (::warning::) plus a human-readable block so
        # the tolerated tool is prominent in both the checks UI and raw logs.
        print("::warning::Tool verification tolerated newly-added tool(s):")
        for item in warnings:
            print(f"::warning::{item}")
        print("Tolerated missing tool(s) (newly added by this PR):")
        for item in warnings:
            print(f"  - {item}")

    if failures:
        print("Tool verification failed:")
        for item in failures:
            print(f"  - {item}")
        return 1

    tiers_str = ", ".join(tiers)
    summary = f"Verified {len(tools)} tool(s) against manifest tiers: {tiers_str}"
    if warnings:
        summary = f"{summary} ({len(warnings)} newly-added tool(s) tolerated)"
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
