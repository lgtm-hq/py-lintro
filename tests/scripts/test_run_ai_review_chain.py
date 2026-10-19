# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""The wrapper walks the chain on a dead lane and isolates each attempt (#2872).

End-to-end through ``scripts/ci/run-ai-review.sh`` with ``gh`` and ``uv``
stubbed: the ``uv`` stub answers ``lintro config --json`` with the row the
wrapper just exported, fails the first review attempt with a chosen error
kind, and on a later attempt writes this run's state and a produced review.
Every invocation records which credentials it could see.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess  # nosec B404 - runs the repo's own script with a fixed argv
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELL_SCRIPT = REPO_ROOT / "scripts" / "ci" / "run-ai-review.sh"
MANIFEST = REPO_ROOT / "scripts" / "ci" / "ai_review_lanes.json"
RUN_ID = "38900000002"
RUN_ATTEMPT = "1"
PR_NUMBER = 2872
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
EMPTY_CODEX_SESSION = base64.b64encode(b"{}").decode("ascii")

_GH_STUB = f"""
set -euo pipefail
log="$CHAIN_STUB_DIR/gh-argv"
printf '%s\\n' "$*" >>"$log"
if [[ "$1" == "label" ]]; then exit 0; fi
if [[ "$1" == "issue" && "$2" == "list" ]]; then
    if [[ -f "$CHAIN_STUB_DIR/open-issues" ]]; then cat "$CHAIN_STUB_DIR/open-issues"; else echo "[]"; fi
    exit 0
fi
if [[ "$1" == "issue" && "$2" == "create" ]]; then echo "https://github.com/lgtm-hq/py-lintro/issues/4242"; exit 0; fi
if [[ "$1" == "issue" && "$2" == "comment" ]]; then exit 0; fi
if [[ "$1" == "run" && "$2" == "download" ]]; then exit 1; fi
[[ "$1" == "api" ]] || exit 1
case "$2" in
    repos/lgtm-hq/py-lintro/pulls/{PR_NUMBER}*)
        printf '{{"state":"open","draft":false,"head":{{"sha":"{HEAD_SHA}","repo":{{"full_name":"lgtm-hq/py-lintro"}}}}}}\\n' ;;
    repos/lgtm-hq/py-lintro/actions/workflows/docker-ci.yml/runs*)
        printf '{{"workflow_runs":[]}}\\n' ;;
    *) exit 1 ;;
esac
"""

_UV_STUB = """
set -u
dir="$CHAIN_STUB_DIR"
if [[ "$*" == *"lintro config --json"* ]]; then
    printf '{"ai":{"provider":"%s","transport":"%s","model":"%s","api_base_url":null,"api_key_env":null,"fallback_models":[]}}\\n' \\
        "${LINTRO_AI_PROVIDER:-}" "${LINTRO_AI_TRANSPORT:-}" "${LINTRO_AI_MODEL:-}"
    exit 0
fi
[[ "$*" == *"lintro review"* ]] || exit 0
n=$(cat "$dir/attempts" 2>/dev/null || echo 0); n=$((n + 1)); printf '%s\\n' "$n" >"$dir/attempts"
{
    printf 'lane=%s\\n' "${LINTRO_REVIEW_LANE:-}"
    printf 'claude=%s\\n' "${CLAUDE_CODE_OAUTH_TOKEN:+set}"
    printf 'anthropic_auth=%s\\n' "${ANTHROPIC_AUTH_TOKEN:+set}"
    printf 'codex_env=%s\\n' "${CODEX_AUTH_JSON:+set}"
    printf 'codex_file=%s\\n' "$([[ -s "$HOME/.codex/auth.json" ]] && echo present || echo absent)"
    printf 'cursor=%s\\n' "${CURSOR_API_KEY:+set}"
    printf 'captured=%s\\n' "$(env | grep -c '^_captured_' || true)"
    printf 'fallback_from=%s\\n' "${LINTRO_REVIEW_FALLBACK_FROM:-}"
    printf 'fallback_reason=%s\\n' "${LINTRO_REVIEW_FALLBACK_REASON:-}"
    printf 'lanes_down=%s\\n' "${LINTRO_REVIEW_LANES_DOWN:-}"
} >"$dir/env-$n"
plan="$(sed -n "${n}p" "$dir/plan")"
case "$plan" in
    fail:*)
        kind="${plan#fail:}"
        count='"completed_chunks":0'
        if [[ "$kind" == *"+chunk" ]]; then
            kind="${kind%+chunk}"
            count='"completed_chunks":1'
            sleep 1
            printf '{"version":5,"run_id":"%s","run_attempt":"%s","runs":[]}' "${GITHUB_RUN_ID:-}" "${GITHUB_RUN_ATTEMPT:-}" >"$LINTRO_REVIEW_STATE_DIR/part-0001.json"
        elif [[ "$kind" == *"+counted" ]]; then
            kind="${kind%+counted}"
            count='"completed_chunks":1'
        elif [[ "$kind" == *"+unknown" ]]; then
            kind="${kind%+unknown}"
            count='"completed_chunks":null'
        fi
        printf '{"error":{"kind":"%s","provider":"%s","status":402,"retryable":false,"provider_unavailable":true,"message":"stub failure",%s}}\\n' "$kind" "${LINTRO_AI_PROVIDER:-}" "$count"
        exit 2 ;;
    ok)
        host="${LINTRO_REVIEW_ENDPOINT#https://}"; host="${host%%/*}"
        verification="verified"
        if [[ "${LINTRO_REVIEW_REPORTS_MODEL_USAGE:-}" != "true" ]]; then verification="unreported"; fi
        printf '{"version":5,"run_id":"%s","run_attempt":"%s","runs":[{"round":1,"lane":"%s","endpoint":"%s","first_party":true,"credential":"x","model_verification":"%s","fallback_from":"%s","fallback_reason":"%s"}]}' \\
            "${GITHUB_RUN_ID:-}" "${GITHUB_RUN_ATTEMPT:-}" "${LINTRO_REVIEW_LANE:-}" "$host" "$verification" "${LINTRO_REVIEW_FALLBACK_FROM:-}" "${LINTRO_REVIEW_FALLBACK_REASON:-}" >"$LINTRO_REVIEW_STATE_DIR/state.json"
        printf '{"readiness_verdict":"ready","coverage":{"complete":true,"covered_at_head":1,"eligible":1},"findings":[]}\\n'
        exit 0 ;;
    *) echo "stub: no plan for attempt $n" >&2; exit 99 ;;
esac
"""


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)


def _run_chain(
    tmp_path: Path,
    *,
    chain: list[str],
    plan: list[str],
    open_issues: list[tuple[int, str]] | None = None,
    without: tuple[str, ...] = (),
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the wrapper with a chain and a per-attempt plan for the review stub.

    Args:
        tmp_path: Per-test scratch directory.
        chain: The resolved chain of manifest lane ids.
        plan: One entry per attempt: ``ok``, ``fail:<kind>`` (count 0),
            ``fail:<kind>+chunk`` (count 1 and a checkpoint part written),
            ``fail:<kind>+counted`` (count 1, no part: the checkpoint write
            failed) or ``fail:<kind>+unknown`` (no count on the envelope).
        open_issues: Open lane-down issues (number, title) the ``gh`` stub
            lists, or ``None`` for none.
        without: Environment names left unset, e.g. a lane's credential.
        overrides: Environment values replaced, e.g. a malformed secret.

    Returns:
        The wrapper's exit code and output, the recorded environment of each
        review attempt, and the ``gh`` calls made.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (stub_dir / "plan").write_text("\n".join(plan) + "\n", encoding="utf-8")
    if open_issues is not None:
        (stub_dir / "open-issues").write_text(
            json.dumps([{"number": n, "title": t} for n, t in open_issues]),
            encoding="utf-8",
        )
    _write_stub(bin_dir, "gh", _GH_STUB)
    _write_stub(bin_dir, "uv", _UV_STUB)
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))["lanes"]
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
        "CHAIN_STUB_DIR": str(stub_dir),
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_RUN_ATTEMPT": RUN_ATTEMPT,
        "GITHUB_TOKEN": "dummy-app-token",  # nosec B105 - test stand-in
        "GH_TOKEN": "dummy-read-token",  # nosec B105 - test stand-in
        "PR_NUMBER": str(PR_NUMBER),
        "LINTRO_REVIEW_STATE_DIR": str(state_dir),
        "LINTRO_REVIEW_CHAIN": json.dumps(chain),
        "LINTRO_REVIEW_LANES": json.dumps({lane: rows[lane] for lane in chain}),
        "CLAUDE_CODE_OAUTH_TOKEN": "dummy-claude-token",  # nosec B105 - test stand-in
        "CURSOR_API_KEY": "dummy-cursor-key",  # nosec B105 - test stand-in
        "CODEX_AUTH_JSON": EMPTY_CODEX_SESSION,
        "LINT_REPORT_WAIT_SECONDS": "0",
        "LINT_REPORT_POLL_SECONDS": "1",
    }
    for name in without:
        env.pop(name, None)
    env.update(overrides or {})
    output = tmp_path / "output.log"
    with output.open("w", encoding="utf-8") as sink:
        completed = subprocess.run(  # nosec B603 - fixed argv run against a repo script in a controlled test; shell=False, no user shell input
            [str(SHELL_SCRIPT)],
            stdout=sink,
            stderr=subprocess.STDOUT,
            check=False,
            env=env,
            timeout=90,
        )
    attempts = []
    for index in range(1, 10):
        recorded = stub_dir / f"env-{index}"
        if not recorded.exists():
            break
        attempts.append(
            dict(
                line.split("=", 1)
                for line in recorded.read_text(encoding="utf-8").splitlines()
                if "=" in line
            ),
        )
    gh_log = stub_dir / "gh-argv"
    gh_calls = (
        gh_log.read_text(encoding="utf-8").splitlines() if gh_log.exists() else []
    )
    return {
        "exit": completed.returncode,
        "output": output.read_text(encoding="utf-8"),
        "attempts": attempts,
        "gh": gh_calls,
        "state_dir": state_dir,
    }


def test_a_dead_primary_lane_falls_back_with_only_the_next_lanes_credential(
    tmp_path: Path,
) -> None:
    """An exhausted balance before any chunk walks to the next lane.

    The second attempt sees the Codex session file and not the Claude
    token, learns which lane died and why, and the run is green with the
    fallback named as a warning.
    """
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai"],
        plan=["fail:insufficient_credits", "ok"],
    )

    assert_that(run["attempts"]).is_length(2)
    first, second = run["attempts"]
    assert_that(first["lane"]).is_equal_to("anthropic")
    assert_that(first["claude"]).is_equal_to("set")
    assert_that(first["codex_file"]).is_equal_to("absent")
    assert_that(second["lane"]).is_equal_to("openai")
    assert_that(second["claude"]).is_empty()
    assert_that(second["codex_env"]).is_empty()
    assert_that(second["codex_file"]).is_equal_to("present")
    assert_that(second["fallback_from"]).is_equal_to("anthropic")
    assert_that(second["fallback_reason"]).is_equal_to("insufficient_credits")
    assert_that(json.loads(second["lanes_down"])).is_equal_to(
        [{"lane": "anthropic", "kind": "insufficient_credits"}],
    )
    # The captured copies never reach the subprocess on either attempt.
    assert_that([a["captured"] for a in run["attempts"]]).is_equal_to(["0", "0"])
    assert_that(run["exit"]).is_equal_to(0)
    assert_that(run["output"]).contains(
        "::warning::lane anthropic unavailable (insufficient_credits) before any chunk completed",
    )
    assert_that(run["output"]).contains(
        "primary lane anthropic unavailable (insufficient_credits); reviewed by openai",
    )
    # The file credential of the fallback lane is gone once the run ends.
    assert_that((tmp_path / "home" / ".codex" / "auth.json").exists()).is_false()


def test_a_dead_lane_opens_one_tracker_issue_or_updates_the_open_one(
    tmp_path: Path,
) -> None:
    """The lane-down tracker is deduplicated by title through the App token."""
    created = _run_chain(
        tmp_path / "create",
        chain=["anthropic", "openai"],
        plan=["fail:auth_failed", "ok"],
    )
    updated = _run_chain(
        tmp_path / "update",
        chain=["anthropic", "openai"],
        plan=["fail:auth_failed", "ok"],
        # A lane id that is a prefix of another must not match its issue.
        open_issues=[
            (4100, "AI review lane down: anthropic-api"),
            (4242, "AI review lane down: anthropic"),
        ],
    )

    creates = [call for call in created["gh"] if call.startswith("issue create ")]
    assert_that(creates).is_length(1)
    assert_that(creates[0]).contains("--label ai-review-lane-down")
    assert_that(creates[0]).contains("AI review lane down: anthropic")
    assert_that(creates[0]).contains("auth_failed")
    comments = [call for call in updated["gh"] if call.startswith("issue comment ")]
    assert_that(comments).is_length(1)
    assert_that(comments[0]).starts_with("issue comment 4242 ")
    assert_that([c for c in updated["gh"] if c.startswith("issue create ")]).is_empty()


def test_a_transient_kind_does_not_walk(tmp_path: Path) -> None:
    """A rate limit stays with the retry, never with another lane."""
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai"],
        plan=["fail:rate_limited", "ok"],
    )

    assert_that(run["attempts"]).is_length(1)
    assert_that(run["exit"]).is_equal_to(1)
    assert_that(run["output"]).contains("not a fallback trigger")
    assert_that(run["output"]).contains("provider unavailable")
    assert_that(run["output"]).does_not_contain("lanes-exhausted")
    assert_that([c for c in run["gh"] if c.startswith("issue ")]).is_empty()


def test_a_lane_that_died_mid_review_is_not_retried_elsewhere(tmp_path: Path) -> None:
    """A checkpoint written during the attempt keeps the round on its lane."""
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai"],
        plan=["fail:insufficient_credits+chunk", "ok"],
    )

    assert_that(run["attempts"]).is_length(1)
    assert_that(run["exit"]).is_equal_to(1)
    assert_that(run["output"]).contains("after a chunk completed")
    assert_that(run["output"]).contains("provider unavailable")
    assert_that(run["output"]).does_not_contain("lanes-exhausted")


@pytest.mark.parametrize("suffix", ["+counted", "+unknown"])
def test_a_counted_or_uncounted_chunk_is_not_retried_elsewhere(
    tmp_path: Path,
    suffix: str,
) -> None:
    """The envelope's count is the witness; a lost checkpoint changes nothing.

    ``+counted`` is a chunk whose checkpoint write failed: no part on disk,
    count 1. ``+unknown`` is a failure with no count at all.
    """
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai"],
        plan=[f"fail:insufficient_credits{suffix}", "ok"],
    )

    assert_that(run["attempts"]).is_length(1)
    assert_that(run["exit"]).is_equal_to(1)
    assert_that(run["output"]).contains("provider unavailable")
    assert_that(run["output"]).does_not_contain("lanes-exhausted")
    assert_that([c for c in run["gh"] if c.startswith("issue ")]).is_empty()


def test_a_three_lane_walk_names_the_first_dead_lane_as_primary(
    tmp_path: Path,
) -> None:
    """Two dead lanes, the third answers: the sticky blames the primary."""
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai", "cursor"],
        plan=["fail:insufficient_credits", "fail:auth_failed", "ok"],
    )

    assert_that(run["attempts"]).is_length(3)
    third = run["attempts"][2]
    assert_that(third["lane"]).is_equal_to("cursor")
    assert_that(third["cursor"]).is_equal_to("set")
    assert_that(third["claude"]).is_empty()
    assert_that(third["codex_file"]).is_equal_to("absent")
    assert_that(third["fallback_from"]).is_equal_to("anthropic")
    assert_that(third["fallback_reason"]).is_equal_to("insufficient_credits")
    assert_that(json.loads(third["lanes_down"])).is_equal_to(
        [
            {"lane": "anthropic", "kind": "insufficient_credits"},
            {"lane": "openai", "kind": "auth_failed"},
        ],
    )
    assert_that(run["exit"]).is_equal_to(0)
    assert_that(run["output"]).contains(
        "primary lane anthropic unavailable (insufficient_credits); reviewed by cursor",
    )
    creates = [c for c in run["gh"] if c.startswith("issue create ")]
    assert_that(creates).is_length(2)


def test_a_fallback_lane_without_a_credential_is_unbound_and_the_walk_goes_on(
    tmp_path: Path,
) -> None:
    """No Codex session configured: openai is recorded unbound, cursor answers.

    The refusal happens inside the loop, after the heartbeat and the traps
    exist, so it must not exit the script: the primary lane's death is still
    classified and the third lane still gets its turn.
    """
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai", "cursor"],
        plan=["fail:insufficient_credits", "ok"],
        without=("CODEX_AUTH_JSON",),
    )

    assert_that(run["attempts"]).is_length(2)
    second = run["attempts"][1]
    assert_that(second["lane"]).is_equal_to("cursor")
    assert_that(second["fallback_from"]).is_equal_to("anthropic")
    assert_that(json.loads(second["lanes_down"])).is_equal_to(
        [
            {"lane": "anthropic", "kind": "insufficient_credits"},
            {"lane": "openai", "kind": "unbound"},
        ],
    )
    assert_that(run["exit"]).is_equal_to(0)
    assert_that(run["output"]).contains("::warning::lane openai could not be bound")
    # The gate's own refusal line is a warning on a fallback attempt: a
    # green run carries no red annotation.
    assert_that(run["output"]).contains(
        "::warning::review lane: no provider credential",
    )
    assert_that(run["output"]).does_not_contain("::error::")
    creates = [c for c in run["gh"] if c.startswith("issue create ")]
    assert_that(creates).is_length(2)
    assert_that(creates[1]).contains("AI review lane down: openai")
    assert_that(creates[1]).contains("could not be bound")


def test_a_malformed_file_secret_in_the_middle_lane_does_not_poison_the_next(
    tmp_path: Path,
) -> None:
    """Openai's Codex secret cannot be decoded: cursor still binds and reviews.

    The restore script fails after the source variable was re-armed; the
    refusal must take that variable back, or cursor is refused as running
    beside a competing credential and a healthy lane reads as exhausted.
    """
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai", "cursor"],
        plan=["fail:auth_failed", "ok"],
        overrides={"CODEX_AUTH_JSON": "not base64 at all !!"},
    )

    assert_that(run["attempts"]).is_length(2)
    second = run["attempts"][1]
    assert_that(second["lane"]).is_equal_to("cursor")
    assert_that(second["cursor"]).is_equal_to("set")
    assert_that(second["codex_env"]).is_empty()
    assert_that(second["codex_file"]).is_equal_to("absent")
    assert_that(run["exit"]).is_equal_to(0)
    assert_that(run["output"]).does_not_contain("competing credentials")
    assert_that(run["output"]).does_not_contain("lanes-exhausted")
    assert_that(json.loads(second["lanes_down"])).is_equal_to(
        [
            {"lane": "anthropic", "kind": "auth_failed"},
            {"lane": "openai", "kind": "unbound"},
        ],
    )


def test_a_last_lane_that_cannot_be_bound_exhausts_the_chain(tmp_path: Path) -> None:
    """Primary dead, the only fallback has no credential: red, both lanes named."""
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai"],
        plan=["fail:auth_failed"],
        without=("CODEX_AUTH_JSON",),
    )

    assert_that(run["attempts"]).is_length(1)
    assert_that(run["exit"]).is_equal_to(1)
    assert_that(run["output"]).contains("status=lanes-exhausted")
    assert_that(run["output"]).contains("anthropic (auth_failed)")
    assert_that(run["output"]).contains("openai (unbound)")
    assert_that(run["output"]).contains("no lane left")
    # Classify ran through the normal tail of the script, not an early exit.
    assert_that(run["output"]).does_not_contain("not invoked")
    # The lane that ran is anthropic: nothing in the verdict is labelled
    # with the unbound lane's credential kind.
    assert_that(run["output"]).does_not_contain("codex_session")


def test_a_file_credential_lane_is_unbound_before_an_env_credential_lane(
    tmp_path: Path,
) -> None:
    """Openai first, then anthropic: the Codex file is gone before Claude binds."""
    run = _run_chain(
        tmp_path,
        chain=["openai", "anthropic"],
        plan=["fail:auth_failed", "ok"],
    )

    assert_that(run["attempts"]).is_length(2)
    first, second = run["attempts"]
    assert_that(first["lane"]).is_equal_to("openai")
    assert_that(first["codex_file"]).is_equal_to("present")
    assert_that(first["claude"]).is_empty()
    assert_that(second["lane"]).is_equal_to("anthropic")
    assert_that(second["codex_file"]).is_equal_to("absent")
    assert_that(second["codex_env"]).is_empty()
    assert_that(second["claude"]).is_equal_to("set")
    assert_that(run["exit"]).is_equal_to(0)
    assert_that(run["output"]).contains(
        "primary lane openai unavailable (auth_failed); reviewed by anthropic",
    )


def test_every_lane_dead_is_lanes_exhausted(tmp_path: Path) -> None:
    """Two lanes, both dead before any chunk: red with its own status."""
    run = _run_chain(
        tmp_path,
        chain=["anthropic", "openai"],
        plan=["fail:insufficient_credits", "fail:auth_failed"],
    )

    assert_that(run["attempts"]).is_length(2)
    assert_that(run["exit"]).is_equal_to(1)
    assert_that(run["output"]).contains("status=lanes-exhausted")
    assert_that(run["output"]).contains("anthropic (insufficient_credits)")
    assert_that(run["output"]).contains("openai (auth_failed)")
    # Both lanes are dead, so both get a tracker.
    creates = [c for c in run["gh"] if c.startswith("issue create ")]
    assert_that(creates).is_length(2)
    assert_that(creates[0]).contains("AI review lane down: anthropic")
    assert_that(creates[1]).contains("AI review lane down: openai")


def test_a_single_lane_chain_keeps_provider_unavailable(tmp_path: Path) -> None:
    """With one lane there is nothing to walk to."""
    run = _run_chain(tmp_path, chain=["anthropic"], plan=["fail:insufficient_credits"])

    assert_that(run["attempts"]).is_length(1)
    assert_that(run["exit"]).is_equal_to(1)
    assert_that(run["output"]).contains("no lane left")
    assert_that(run["output"]).contains("provider unavailable")
    assert_that(run["output"]).does_not_contain("lanes-exhausted")
    # The one lane is still dead and still tracked.
    creates = [c for c in run["gh"] if c.startswith("issue create ")]
    assert_that(creates).is_length(1)


def test_a_review_on_the_primary_lane_walks_nowhere(tmp_path: Path) -> None:
    """The happy path makes one attempt and tracks nothing."""
    run = _run_chain(tmp_path, chain=["anthropic", "openai"], plan=["ok"])

    assert_that(run["attempts"]).is_length(1)
    assert_that(run["exit"]).is_equal_to(0)
    assert_that(run["output"]).does_not_contain("::warning::lane")
    assert_that([c for c in run["gh"] if c.startswith("issue ")]).is_empty()
