#!/usr/bin/env python3
"""Opt the Cursor agent CLI into ``--trust`` for one CI checkout.

``lintro review`` with ``LINTRO_AI_PROVIDER=cursor`` shells out to ``agent``,
which refuses to start in a non-interactive runner until the workspace is
trusted (``--trust``, ``--yolo``, or ``-f``). lintro's opt-in for that flag is
``ai.cursor_trust_workspace``; there is no env or CLI overlay for it (#1970).

The dogfood job checks out the PR's trusted BASE ref and never runs for fork
PRs, so this is the case the flag exists for. The committed
``.lintro-config.yaml`` stays ``false`` so a local checkout does not silently
grant workspace trust.

Stdlib only: this runs on the runner before ``uv sync``.

Usage:
    python3 scripts/ci/enable_cursor_workspace_trust.py
    python3 scripts/ci/enable_cursor_workspace_trust.py --config .lintro-config.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_CONFIG = ".lintro-config.yaml"

_TRUE_LINE = "  cursor_trust_workspace: true"
# Do not let ``\s`` eat the line's trailing newline — that would strip it
# from the file on a last-line match.
_KEY_LINE_RE = re.compile(
    r"^  cursor_trust_workspace:\s*\S+[ \t]*$",
    re.MULTILINE,
)
_AI_HEADER_RE = re.compile(r"^ai:[ \t]*$", re.MULTILINE)


def enable_cursor_workspace_trust(*, text: str) -> str:
    """Return YAML text with ``ai.cursor_trust_workspace`` set to true.

    Args:
        text: Full ``.lintro-config.yaml`` contents.

    Returns:
        The updated document. Unchanged when the key is already true.

    Raises:
        ValueError: When there is no top-level ``ai:`` mapping to attach to.
    """
    if re.search(r"^  cursor_trust_workspace:\s*true\s*$", text, flags=re.MULTILINE):
        return text

    replaced, count = _KEY_LINE_RE.subn(_TRUE_LINE, text, count=1)
    if count:
        return replaced

    inserted, count = _AI_HEADER_RE.subn(f"ai:\n{_TRUE_LINE}", text, count=1)
    if count:
        return inserted

    msg = "no top-level `ai:` section found; cannot set cursor_trust_workspace"
    raise ValueError(msg)


def main(*, argv: list[str] | None = None) -> int:
    """Patch the config file in place.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (``0`` on success).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Path to .lintro-config.yaml (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    path = Path(args.config)
    if not path.is_file():
        print(f"config not found: {path}", file=sys.stderr)
        return 1

    original = path.read_text(encoding="utf-8")
    try:
        updated = enable_cursor_workspace_trust(text=original)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if updated != original:
        path.write_text(updated, encoding="utf-8")
        print(f"Set ai.cursor_trust_workspace: true in {path}")
    else:
        print(f"ai.cursor_trust_workspace already true in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
