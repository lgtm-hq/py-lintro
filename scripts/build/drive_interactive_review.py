#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Drive the built binary's interactive AI review through a pty (#2514).

``lintro`` and ``pygments`` ship as bytecode rather than compiled C, and the
pygments lexers are resolved by name at runtime, so a packaging mistake there
is invisible to ``--version``, ``--help`` and the tool-registry smoke test:
the first person to review an AI fix would find it. This driver closes that
gap inside the verify step.

It runs ``check --fix --yes`` on a throwaway file with the fixtures under
``scripts/build/fixtures`` first on ``PATH`` (a fake ``ruff`` that reports two
unused imports and a fake ``claude`` that offers a behavioural-risk fix for
each, so the run routes to interactive review), answers the ``[q]quit:``
prompt with ``d`` then ``q``, and fails unless the diff came back
pygments-highlighted -- ANSI colour on the ``+``/``-`` lines.

The review needs a real terminal (``click.getchar``), so the binary runs under
a pty rather than a pipe.

Usage:
    python3 scripts/build/drive_interactive_review.py dist/nuitka/lintro
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = SCRIPT_DIR / "fixtures"

#: Prompt emitted once per fix group by ``review_fixes_interactive``.
PROMPT_MARKER = b"[q]quit:"

#: Keys answered at successive prompts: show the diffs, then quit.
REVIEW_KEYS = (b"d", b"q")

#: Seconds to wait for the whole session, including onefile extraction.
SESSION_TIMEOUT_SECONDS = 300

#: A pygments-highlighted diff line: an SGR sequence immediately followed by
#: the diff marker. A degraded (plain-text) render emits the marker bare.
COLOURED_ADDITION = re.compile(rb"\x1b\[[0-9;]*m\+")
COLOURED_REMOVAL = re.compile(rb"\x1b\[[0-9;]*m-")

SAMPLE_FILE = "bad.py"
SAMPLE_SOURCE = "import os\nimport sys\n\n\nVALUE = 1\n"

#: AI is off by default, and the fix path needs the CLI transport pointed at
#: the fake provider. Written into the throwaway workspace so nothing outside
#: it is configured.
CONFIG_FILE = ".lintro-config.yaml"
CONFIG_SOURCE = """ai:
  enabled: true
  lint: true
  provider: anthropic
  transport: cli
"""


def build_environment() -> dict[str, str]:
    """Build the child environment with the CLI fixtures first on ``PATH``.

    Returns:
        Environment mapping for the pty child.
    """
    env = dict(os.environ)
    fixture_dirs = [
        str(FIXTURES_DIR / "fake-claude"),
        str(FIXTURES_DIR / "fake-ruff"),
    ]
    env["PATH"] = os.pathsep.join([*fixture_dirs, env.get("PATH", "")])
    # Deterministic rendering: a narrow or absent COLUMNS would wrap the diff,
    # and NO_COLOR would disable the very highlighting under test.
    env["COLUMNS"] = "120"
    env.pop("NO_COLOR", None)
    # Hermetic: a developer's ~/.lintro-config.yaml must not reach this run.
    env["LINTRO_GLOBAL_CONFIG"] = "off"
    return env


def drive(binary: Path, workspace: Path) -> tuple[bytes, int]:
    """Run the review under a pty and answer its prompts.

    Args:
        binary: Path to the built lintro binary.
        workspace: Directory holding the sample file to review.

    Returns:
        Everything the child wrote, and the number of keys delivered.
    """
    argv = [
        str(binary),
        "check",
        "--fix",
        "--yes",
        "--transport",
        "cli",
        "--tools",
        "ruff",
        "--no-art",
        SAMPLE_FILE,
    ]
    env = build_environment()
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child replaces itself
        os.chdir(workspace)
        try:
            os.execvpe(argv[0], argv, env)
        finally:
            # A failed exec must not fall through into the parent's code.
            os._exit(127)  # noqa: SLF001 - the only correct exit in a fork child

    captured = b""
    sent = 0
    deadline = time.time() + SESSION_TIMEOUT_SECONDS
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        captured += chunk
        while sent < len(REVIEW_KEYS) and captured.count(PROMPT_MARKER) > sent:
            time.sleep(0.3)
            os.write(fd, REVIEW_KEYS[sent])
            sent += 1

    os.close(fd)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    return captured, sent


def diff_was_highlighted(captured: bytes) -> bool:
    """Report whether a pygments-rendered diff appeared in the output.

    Args:
        captured: Everything the child wrote to the pty.

    Returns:
        True when both an added and a removed line carry ANSI colour.
    """
    return bool(
        COLOURED_ADDITION.search(captured) and COLOURED_REMOVAL.search(captured),
    )


def main() -> int:
    """Drive the interactive review and report the verdict.

    Returns:
        Exit code (0 when a highlighted diff was rendered).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary", type=Path, help="Path to the built binary")
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file():
        print(f"FAIL interactive review: binary not found: {binary}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        (workspace / SAMPLE_FILE).write_text(SAMPLE_SOURCE, encoding="utf-8")
        (workspace / CONFIG_FILE).write_text(CONFIG_SOURCE, encoding="utf-8")
        captured, sent = drive(binary, workspace)

    transcript = captured.decode("utf-8", errors="replace")
    if sent < len(REVIEW_KEYS):
        print(transcript, file=sys.stderr)
        print(
            "FAIL interactive review: the review prompt appeared "
            f"{sent} of {len(REVIEW_KEYS)} times",
            file=sys.stderr,
        )
        return 1

    if not diff_was_highlighted(captured):
        print(transcript, file=sys.stderr)
        print(
            "FAIL interactive review: the diff rendered without pygments "
            "highlighting (no ANSI colour on the +/- lines)",
            file=sys.stderr,
        )
        return 1

    print(f"OK interactive review: pygments-highlighted diff, {len(captured)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
