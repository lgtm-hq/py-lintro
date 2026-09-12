"""Tier 1 replay: recorded CLI output must still parse to the same response.

Free by construction — no binary, no credential, no quota — so this runs on
every PR, which is the point (#2600). The recordings under
``tests/fixtures/ai/cli_replay`` hold one stdout capture per supported agent
CLI at the pinned version; parsing them with the real transport parsers is what
turns a vendor schema change into a red diff instead of a broken review in
production.

The committed captures are hand-authored to the schema each parser documents,
because recording real output needs the CLIs installed and a credential. Until
an owner re-records, this guards our parsers rather than proving vendor
output.

The expectation files pin the cost the parser attributed to each call as
well, so a break in cost extraction is caught here rather than falling back to
an estimate that still looks plausible downstream. Only the codex golden is
priced from lintro's own table, so a pricing change asks for a re-record of
that one; claude's cost is read from the capture and cursor's parser reports a
flat zero.

Re-record with ``scripts/ci/record_cli_fixture.sh`` when a CLI pin moves.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from assertpy import assert_that

from tests.unit.ai.providers.cli_replay import (
    FIXTURE_ROOT,
    expected_path,
    recordings,
    replay,
    supported_clis,
)

_RECORDINGS = recordings()

#: The Dockerfile build arg that pins each CLI. The recording's file name must
#: match *its own* CLI's pin: searching the whole Dockerfile for the version
#: string would let a comment, or another tool that happens to share the
#: version, keep the test green while the pin that matters moved.
_PIN_ARGS: dict[str, str] = {
    "claude": "CLAUDE_CODE_VERSION",
    "codex": "CODEX_VERSION",
    "cursor": "CURSOR_AGENT_VERSION",
}


def test_every_supported_cli_has_a_recording() -> None:
    """A supported CLI without a recording is an unguarded parser."""
    covered = {cli for cli, _ in _RECORDINGS}
    assert_that(sorted(covered)).is_equal_to(sorted(supported_clis()))


def test_recordings_are_versioned_by_the_pinned_cli_version() -> None:
    """Each recording is named for the CLI version that produced it.

    Without the version in the name, nobody can tell whether a fixture still
    describes the binary the image ships, and re-recording becomes guesswork.
    """
    pins = (
        Path(__file__).resolve().parents[4] / "docker" / "ai-tools.Dockerfile"
    ).read_text(
        encoding="utf-8",
    )
    for cli, recording in _RECORDINGS:
        version = recording.name.removesuffix(".jsonl")
        arg = _PIN_ARGS.get(cli)
        assert_that(arg).described_as(f"{cli} has no known pin arg").is_not_none()
        match = re.search(
            rf"^ARG {arg}=(?P<version>\S+)\s*$",
            pins,
            flags=re.MULTILINE,
        )
        assert_that(match).described_as(
            f"docker/ai-tools.Dockerfile must pin {arg}",
        ).is_not_none()
        assert_that(match.group("version")).described_as(  # type: ignore[union-attr]
            f"{cli} recording {version} must match the {arg} pin",
        ).is_equal_to(version)


@pytest.mark.parametrize(
    ("cli", "recording"),
    _RECORDINGS,
    ids=[f"{cli}-{path.name}" for cli, path in _RECORDINGS],
)
def test_recording_parses_to_the_expected_response(cli: str, recording: Path) -> None:
    """The real parser must turn each recording into the recorded shape.

    Args:
        cli: CLI the recording came from.
        recording: Path of the recorded stdout.
    """
    expected = json.loads(
        expected_path(recording=recording).read_text(encoding="utf-8"),
    )
    result = replay(
        cli=cli,
        stdout=recording.read_text(encoding="utf-8"),
        model=expected["model"],
    )

    assert_that(result.as_dict()).is_equal_to(expected)
    # Spelled out as well as compared: an expectation file edited to an empty
    # content field would otherwise still "pass" this test.
    assert_that(result.content).is_not_empty()
    assert_that(result.input_tokens).is_greater_than(0)
    assert_that(result.output_tokens).is_greater_than(0)


def test_fixture_root_is_where_the_recorder_writes() -> None:
    """The helper and the fixtures must not drift apart on location."""
    assert_that(str(FIXTURE_ROOT)).ends_with("tests/fixtures/ai/cli_replay")
    assert_that(FIXTURE_ROOT.is_dir()).is_true()


def test_a_recording_under_an_unknown_cli_is_an_error_not_a_skip(
    tmp_path: Path,
) -> None:
    """A fixture nobody parses must fail loudly, not vanish from the run.

    Dropping it silently is the exact hole this fixture set exists to close:
    a recording added for a CLI whose parser was never wired up would leave
    the suite green while guarding nothing.

    Args:
        tmp_path: Temporary fixture root.
    """
    (tmp_path / "gemini").mkdir()
    (tmp_path / "gemini" / "1.0.0.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="gemini"):
        recordings(root=tmp_path)
