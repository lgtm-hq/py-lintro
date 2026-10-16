# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""The chain decider advances only on the dead-lane class, before any chunk.

ADR-0011 decision D (#2872): exactly ``auth_failed``, ``insufficient_credits``
and ``quota_exceeded``, only on the review error status, only when no
checkpoint part was written during the attempt, and only while a lane is
left. Everything else stops with its reason.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess  # nosec B404 - runs the repo's own script with a fixed argv
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

from lintro.ai.review.error_contract import UNAVAILABLE_KINDS
from lintro.ai.review.errors_taxonomy import ReviewErrorKind

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "review_chain.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("review_chain", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def chain() -> ModuleType:
    """Return the decider module."""
    return _load()


def _envelope(kind: str) -> str:
    payload = {
        "error": {
            "kind": kind,
            "provider": "anthropic",
            "status": 402,
            "retryable": False,
            "provider_unavailable": True,
            "message": "balance too low",
        },
    }
    return f"log line\n{json.dumps(payload)}\ntrailing\n"


def _marker(tmp_path: Path) -> Path:
    marker = tmp_path / "attempt-started"
    marker.write_text("", encoding="utf-8")
    # Make the marker older than anything written next.
    old = time.time() - 5
    os.utime(marker, (old, old))
    return marker


def test_the_trigger_set_is_a_strict_subset_of_the_unavailable_kinds(
    chain: ModuleType,
) -> None:
    """The walk keys on the dead-credential class, not the whole unavailable set."""
    unavailable = {kind.value for kind in UNAVAILABLE_KINDS}
    assert_that(chain.FALLBACK_KINDS).is_equal_to(
        {
            ReviewErrorKind.AUTH_FAILED.value,
            ReviewErrorKind.INSUFFICIENT_CREDITS.value,
            ReviewErrorKind.QUOTA_EXCEEDED.value,
        },
    )
    assert_that(chain.FALLBACK_KINDS).is_subset_of(unavailable)
    assert_that(unavailable - chain.FALLBACK_KINDS).contains(
        "rate_limited",
        "server_error",
        "timeout",
        "provider_unavailable",
    )


@pytest.mark.parametrize(
    "kind",
    ["auth_failed", "insufficient_credits", "quota_exceeded"],
)
def test_a_dead_lane_before_any_chunk_advances(
    chain: ModuleType,
    tmp_path: Path,
    kind: str,
) -> None:
    """The review error status with a trigger kind and no part written: advance."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    verb, detail = chain.decide(
        status=2,
        output=_envelope(kind),
        state_dir=state_dir,
        since=_marker(tmp_path),
        attempt=0,
        chain_length=2,
    )

    assert_that(verb).is_equal_to(chain.ADVANCE)
    assert_that(detail).is_equal_to(kind)


@pytest.mark.parametrize(
    ("status", "kind", "fragment"),
    [
        (0, "insufficient_credits", "not the error status"),
        (1, "insufficient_credits", "not the error status"),
        (2, "rate_limited", "not a fallback trigger"),
        (2, "server_error", "not a fallback trigger"),
        (2, "timeout", "not a fallback trigger"),
        (2, "provider_unavailable", "not a fallback trigger"),
        (2, "", "not a fallback trigger"),
    ],
)
def test_other_outcomes_stop(
    chain: ModuleType,
    tmp_path: Path,
    status: int,
    kind: str,
    fragment: str,
) -> None:
    """A produced review, a transient kind, or no envelope never walks."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    output = _envelope(kind) if kind else "no envelope at all\n"

    verb, detail = chain.decide(
        status=status,
        output=output,
        state_dir=state_dir,
        since=_marker(tmp_path),
        attempt=0,
        chain_length=3,
    )

    assert_that(verb).is_equal_to(chain.STOP)
    assert_that(detail).contains(fragment)


def test_a_completed_chunk_stops_the_walk(chain: ModuleType, tmp_path: Path) -> None:
    """A part written during the attempt means the round stays on its lane."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    marker = _marker(tmp_path)
    (state_dir / "part-0001.json").write_text("{}", encoding="utf-8")

    verb, detail = chain.decide(
        status=2,
        output=_envelope("insufficient_credits"),
        state_dir=state_dir,
        since=marker,
        attempt=0,
        chain_length=2,
    )

    assert_that(verb).is_equal_to(chain.STOP)
    assert_that(detail).contains("after a chunk completed")


def test_a_part_older_than_the_attempt_does_not_count(
    chain: ModuleType,
    tmp_path: Path,
) -> None:
    """A downloaded prior-round part predates the marker and is not this attempt's."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    stale = state_dir / "part-0001.json"
    stale.write_text("{}", encoding="utf-8")
    old = time.time() - 60
    os.utime(stale, (old, old))
    marker = _marker(tmp_path)

    verb, _detail = chain.decide(
        status=2,
        output=_envelope("auth_failed"),
        state_dir=state_dir,
        since=marker,
        attempt=0,
        chain_length=2,
    )

    assert_that(verb).is_equal_to(chain.ADVANCE)


@pytest.mark.parametrize(("attempt", "chain_length"), [(1, 2), (0, 1), (2, 3)])
def test_a_dead_last_lane_is_exhausted(
    chain: ModuleType,
    tmp_path: Path,
    attempt: int,
    chain_length: int,
) -> None:
    """No lane left: the lane is still dead, and the wrapper records it so."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    verb, detail = chain.decide(
        status=2,
        output=_envelope("quota_exceeded"),
        state_dir=state_dir,
        since=_marker(tmp_path),
        attempt=attempt,
        chain_length=chain_length,
    )

    assert_that(verb).is_equal_to(chain.EXHAUSTED)
    assert_that(detail).is_equal_to("quota_exceeded")


def test_a_missing_marker_is_read_as_a_completed_chunk(
    chain: ModuleType,
    tmp_path: Path,
) -> None:
    """Without the marker there is no way to tell, so the walk does not advance."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    verb, detail = chain.decide(
        status=2,
        output=_envelope("auth_failed"),
        state_dir=state_dir,
        since=tmp_path / "never-created",
        attempt=0,
        chain_length=2,
    )

    assert_that(verb).is_equal_to(chain.STOP)
    assert_that(detail).contains("after a chunk completed")


def test_the_last_envelope_wins(chain: ModuleType, tmp_path: Path) -> None:
    """Interleaved logs may carry an earlier envelope; the last one is the attempt's."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    output = _envelope("rate_limited") + _envelope("auth_failed")

    verb, detail = chain.decide(
        status=2,
        output=output,
        state_dir=state_dir,
        since=_marker(tmp_path),
        attempt=0,
        chain_length=2,
    )

    assert_that(verb).is_equal_to(chain.ADVANCE)
    assert_that(detail).is_equal_to("auth_failed")


def test_lanes_down_appends_and_validates(chain: ModuleType) -> None:
    """The dead-lane list grows by one validated entry; junk is refused."""
    first = chain.lanes_down("[]", lane="anthropic", kind="insufficient_credits")
    second = chain.lanes_down(first, lane="openai", kind="auth_failed")

    assert_that(json.loads(second)).is_equal_to(
        [
            {"lane": "anthropic", "kind": "insufficient_credits"},
            {"lane": "openai", "kind": "auth_failed"},
        ],
    )
    for bad_lane in ("", "zai\nlane", "a b", "x" * 65, "lane;rm"):
        with pytest.raises(ValueError, match="not a lane id"):
            chain.lanes_down("[]", lane=bad_lane, kind="auth_failed")
    with pytest.raises(ValueError, match="not an error kind"):
        chain.lanes_down("[]", lane="anthropic", kind="auth failed")
    with pytest.raises(ValueError, match="not JSON"):
        chain.lanes_down("not json", lane="anthropic", kind="auth_failed")
    with pytest.raises(ValueError, match="not a list"):
        chain.lanes_down('{"lane": 1}', lane="anthropic", kind="auth_failed")


def test_the_script_prints_one_decision_line(tmp_path: Path) -> None:
    """Run as the wrapper does: one ``<verb> <detail>`` line."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    output_file = tmp_path / "review.log"
    output_file.write_text(_envelope("insufficient_credits"), encoding="utf-8")
    marker = _marker(tmp_path)
    base = [sys.executable, "-I", str(SCRIPT)]

    advance = subprocess.run(  # nosec B603 - fixed argv, the repo's own script
        [
            *base,
            "decide",
            "--status",
            "2",
            "--output-file",
            str(output_file),
            "--state-dir",
            str(state_dir),
            "--since",
            str(marker),
            "--attempt",
            "0",
            "--chain-length",
            "2",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    down = subprocess.run(  # nosec B603 - fixed argv, the repo's own script
        [
            *base,
            "lanes-down",
            "--current",
            "[]",
            "--lane",
            "anthropic",
            "--kind",
            "auth_failed",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    refused = subprocess.run(  # nosec B603 - fixed argv, the repo's own script
        [
            *base,
            "lanes-down",
            "--current",
            "[]",
            "--lane",
            "a b",
            "--kind",
            "auth_failed",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(advance.returncode).is_equal_to(0)
    assert_that(advance.stdout).is_equal_to("advance insufficient_credits\n")
    assert_that(down.stdout.strip()).is_equal_to(
        '[{"lane":"anthropic","kind":"auth_failed"}]',
    )
    assert_that(refused.returncode).is_equal_to(1)
    assert_that(refused.stderr).contains("::error::review chain:")


def test_the_decider_is_stdlib_only() -> None:
    """It runs from a bare python3 like the classifier."""
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.startswith(("import ", "from ")):
            assert_that(line).does_not_contain("lintro")
