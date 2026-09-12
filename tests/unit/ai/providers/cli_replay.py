"""Replay recorded agent-CLI output through lintro's own transport parsers.

Nothing in this repo used to exercise CLI output parsing against real recorded
output, so schema drift was discovered in production — the Codex lane needed
three successive fixes for exactly that (#2600). The fixtures under
``tests/fixtures/ai/cli_replay/<cli>/<version>.jsonl`` hold one recording per
supported CLI, and the Tier 1 (free, every-PR) replay test parses each of them
with the *real* transport parser, so a renamed field is a PR-time diff instead
of a broken review.

This module is the seam both consumers share: the replay test, and
``scripts/ci/record_cli_fixture.sh``, which regenerates a recording's
``.expected.json`` after a CLI version bump.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from lintro.ai.providers.anthropic.provider import _AnthropicCliTransport
from lintro.ai.providers.cursor.provider import _CursorCliTransport
from lintro.ai.providers.openai.provider import _CodexCliTransport
from lintro.ai.providers.response import AIResponse

#: Root of the committed recordings.
FIXTURE_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[3] / "fixtures" / "ai" / "cli_replay"
)

#: Placeholder binary path. The parsers never touch the filesystem, and a
#: replay must not depend on the CLI being installed — that is Tier 2's job.
_FAKE_BINARY: Final[str] = "/nonexistent/replay-binary"


@dataclass(frozen=True)
class ReplayResult:
    """The normalized shape a recording parses to.

    Attributes:
        content: Response text the parser extracted.
        model: Model the transport was constructed with.
        input_tokens: Prompt tokens the parser read from the recording.
        output_tokens: Completion tokens the parser read.
        session_id: Session id the parser recovered, when the CLI reports one.
    """

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    session_id: str | None

    def as_dict(self) -> dict[str, object]:
        """Return the result in the shape the ``.expected.json`` files use.

        Returns:
            A JSON-serializable mapping of the normalized fields.
        """
        return {
            "model": self.model,
            "content": self.content,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "session_id": self.session_id,
        }


def supported_clis() -> tuple[str, ...]:
    """Return the CLI names a recording can be replayed for.

    Returns:
        The CLI identifiers, matching the fixture directory names.
    """
    return ("claude", "codex", "cursor")


def _parse(*, cli: str, stdout: str, model: str) -> tuple[AIResponse, str | None]:
    """Parse recorded stdout with the real transport parser for one CLI.

    Args:
        cli: CLI identifier, one of :func:`supported_clis`.
        stdout: The recorded stdout, verbatim.
        model: Model to construct the transport with; the CLIs report none.

    Returns:
        The parsed response and the session id, which codex never reports.

    Raises:
        ValueError: When ``cli`` is not a supported CLI.
    """
    if cli == "claude":
        transport = _AnthropicCliTransport(binary_path=_FAKE_BINARY, model=model)
        return transport.parse_stdout(stdout)
    if cli == "cursor":
        cursor = _CursorCliTransport(binary_path=_FAKE_BINARY, model=model)
        return cursor.parse_stdout(stdout)
    if cli == "codex":
        codex = _CodexCliTransport(binary_path=_FAKE_BINARY, model=model)
        return codex.parse_stdout_as(stdout, model=model), None
    msg = f"unsupported CLI {cli!r}; known: {', '.join(supported_clis())}"
    raise ValueError(msg)


def replay(*, cli: str, stdout: str, model: str) -> ReplayResult:
    """Replay one recording and return the normalized response.

    Args:
        cli: CLI identifier, one of :func:`supported_clis`.
        stdout: The recorded stdout, verbatim.
        model: Model to construct the transport with.

    Returns:
        The normalized result.
    """
    response, session_id = _parse(cli=cli, stdout=stdout, model=model)
    return ReplayResult(
        content=response.content,
        model=response.model,
        input_tokens=int(response.input_tokens),
        output_tokens=int(response.output_tokens),
        session_id=session_id,
    )


def recordings(*, root: Path = FIXTURE_ROOT) -> list[tuple[str, Path]]:
    """Return every committed recording as a ``(cli, path)`` pair.

    Args:
        root: Fixture root to scan.

    Returns:
        Sorted ``(cli, recording path)`` pairs.
    """
    found = [
        (path.parent.name, path)
        for path in sorted(root.rglob("*.jsonl"))
        if path.parent.name in supported_clis()
    ]
    return found


def expected_path(*, recording: Path) -> Path:
    """Return the expectation file that belongs to a recording.

    Args:
        recording: Path of the ``<version>.jsonl`` recording.

    Returns:
        The sibling ``<version>.expected.json`` path.
    """
    return recording.with_suffix(".expected.json")


def main(argv: list[str] | None = None) -> int:
    """Regenerate a recording's expectation file.

    Used by ``scripts/ci/record_cli_fixture.sh`` after a CLI version bump: the
    recording is the evidence, the expectation is what the parser makes of it,
    and both are committed so the next drift shows up as a diff.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description="Rewrite a replay expectation")
    parser.add_argument("--cli", required=True, choices=supported_clis())
    parser.add_argument("--recording", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)

    recording = Path(args.recording)
    result = replay(
        cli=args.cli,
        stdout=recording.read_text(encoding="utf-8"),
        model=args.model,
    )
    target = expected_path(recording=recording)
    target.write_text(
        json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
