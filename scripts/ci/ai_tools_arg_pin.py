#!/usr/bin/env python3
"""Resolve a pinned build ARG from ``docker/ai-tools.Dockerfile``.

The agent-CLI versions lintro ships are pinned exactly once, as ``ARG`` lines in
the ai-tools Dockerfile, where Renovate keeps them current. A workflow that needs
one of those versions on a bare runner — the AI review dogfood installs the
``claude`` CLI directly rather than pulling the image — reads it from here rather
than copying the number into the workflow, because a second pin site drifts and a
review running a *different* CLI version than the one lintro ships is exactly the
drift the contract tests exist to catch (#1611, #1614).

Stdlib only and no lintro import: this runs before any dependency install.

Usage:
    scripts/ci/ai_tools_arg_pin.py CLAUDE_CODE_VERSION
    scripts/ci/ai_tools_arg_pin.py NODE_VERSION CLAUDE_CODE_VERSION \
        --exact --format github

The default output is one bare value per line, in the order requested.
``--format github`` emits ``lowercase-dashed-name=value`` lines, ready to append
to ``$GITHUB_OUTPUT``. ``--exact`` refuses anything that is not a literal
``X.Y.Z`` version, which is what a caller feeding the value to ``npm install`` or
``setup-node`` needs: ``latest`` and ``^2.1.220`` are non-empty, so an emptiness
check alone would happily forward a moving target. Not every ARG in the file is
a semver (``CURSOR_AGENT_VERSION`` is a calendar build ID), so it is opt-in.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_DOCKERFILE = "docker/ai-tools.Dockerfile"

#: A literal three-component version and nothing else — no ranges, no dist-tags,
#: no npm aliases.
EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def build_arg_pattern(*, name: str) -> re.Pattern[str]:
    """Build the regex matching an ``ARG <name>=<value>`` line.

    Args:
        name: Build-argument name to match.

    Returns:
        A compiled pattern whose first group is the pinned value.
    """
    return re.compile(rf"^\s*ARG\s+{re.escape(name)}=(\S+)\s*$")


def resolve_arg(*, dockerfile_text: str, name: str, exact: bool = False) -> str:
    """Extract the default value of a build ARG.

    Args:
        dockerfile_text: Full Dockerfile contents.
        name: Build-argument name whose default value is wanted.
        exact: When true, require the value to be a literal ``X.Y.Z`` version.

    Returns:
        The pinned value exactly as written in the Dockerfile.

    Raises:
        ValueError: When the ARG is absent, carries no default value, or —
            under *exact* — is a dist-tag or range rather than a fixed version.
            A missing or moving pin must fail loudly: silently forwarding
            ``latest`` would install an unpinned CLI into a credentialed job.
    """
    pattern = build_arg_pattern(name=name)
    for line in dockerfile_text.splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        value = match.group(1)
        if exact and not EXACT_VERSION_RE.match(value):
            msg = (
                f"ARG {name}={value} is not an exact X.Y.Z version. "
                "A range or dist-tag would let the installed version move."
            )
            raise ValueError(msg)
        return value

    msg = f"no `ARG {name}=<value>` line found in the Dockerfile"
    raise ValueError(msg)


def format_line(*, name: str, value: str, output_format: str) -> str:
    """Render one resolved pin in the requested output format.

    Args:
        name: Build-argument name as requested on the command line.
        value: Resolved pin value.
        output_format: Either ``value`` (bare) or ``github`` (``key=value``).

    Returns:
        The line to print, without a trailing newline.
    """
    if output_format == "github":
        return f"{name.lower().replace('_', '-')}={value}"
    return value


def main(*, argv: list[str] | None = None) -> int:
    """Print the pinned value for each requested build ARG.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (``0`` on success).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "names",
        nargs="+",
        help="Build-argument names to resolve, in output order.",
    )
    parser.add_argument(
        "--dockerfile",
        default=DEFAULT_DOCKERFILE,
        help="Path to the Dockerfile holding the pins.",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="Require each resolved value to be a literal X.Y.Z version.",
    )
    parser.add_argument(
        "--format",
        choices=("value", "github"),
        default="value",
        dest="output_format",
        help="Output format: bare values, or GITHUB_OUTPUT key=value lines.",
    )
    args = parser.parse_args(argv)

    path = Path(args.dockerfile)
    if not path.exists():
        print(f"Dockerfile not found: {path}", file=sys.stderr)
        return 1

    dockerfile_text = path.read_text(encoding="utf-8")
    lines: list[str] = []
    for name in args.names:
        try:
            value = resolve_arg(
                dockerfile_text=dockerfile_text,
                name=name,
                exact=args.exact,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        lines.append(
            format_line(name=name, value=value, output_format=args.output_format),
        )

    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
