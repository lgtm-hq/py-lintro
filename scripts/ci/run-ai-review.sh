#!/usr/bin/env bash
set -euo pipefail

# run-ai-review.sh
#
# Dogfood `lintro review` on py-lintro's own pull requests. Runs an AI diff
# review over the PR, posts a rich sticky comment (and inline findings) to the
# PR via `--post`, and prints the JSON result to the log.
#
# NO-SILENT-SKIP (#1826): this script used to always exit 0, so the `AI Review`
# check read green even when the review aborted — for months it reported success
# while producing nothing, because the Anthropic balance was depleted. It now
# exits non-zero whenever no review was produced:
#
#   exit 0  a review was produced (findings or clean), or the convergence stop
#           rule (#2099) deliberately skipped the round with nothing open that
#           blocks — so exit 0 is "not blocked", not "a review ran"
#   exit 1  no review was produced — no credential, dead credential, depleted
#           balance, unreachable provider, or a lintro-side failure — or a
#           converged skip whose last real round left open P1 findings
#
# The check is deliberately NOT required, so a billing condition is loud without
# blocking a merge. P1 findings from a completed round still exit 0: the review
# ran, and findings are advisory here.
#
# Classification lives in scripts/ci/classify_review_outcome.py, which reads
# lintro's own machine-readable error envelope — the failure taxonomy is not
# re-implemented in shell.
#
# Default transport is `cli` (workflow fallback when LINTRO_AI_TRANSPORT is
# unset). The credential depends on LINTRO_AI_PROVIDER (#1971): anthropic uses
# CLAUDE_CODE_OAUTH_TOKEN (the `claude` CLI OAuth session, not ANTHROPIC_API_KEY
# whose account has no balance — #1894); cursor uses CURSOR_API_KEY. Checking
# the wrong variable would report "no credential" on a perfectly authenticated
# run, and vice versa.
#
# Trusted install: the workflow checks out the PR's BASE ref (main) before
# invoking this script, so the lintro that runs with the provider credential is
# trusted code — never the PR head. The PR diff is fetched independently by
# `lintro review --pr` via `gh` (GitHub API), so the PR's changes are reviewed
# as data and never executed with the token.
#
# Fork PRs never reach this script: the workflow's job guard requires the head
# repo to be the base repo, so an empty credential means the secret is
# genuinely missing — a visible failure, not a skip.
#
# Usage:
#   PR_NUMBER=<n> CLAUDE_CODE_OAUTH_TOKEN=<token> GH_TOKEN=<token> \
#     scripts/ci/run-ai-review.sh
#   scripts/ci/run-ai-review.sh <pr-number>
#
# Environment:
#   CLAUDE_CODE_OAUTH_TOKEN Claude Code OAuth token used by the `claude` CLI.
#                           Required when LINTRO_AI_PROVIDER is anthropic
#                           (the default). Empty => visible failure.
#   CURSOR_API_KEY          Cursor CLI key. Required when LINTRO_AI_PROVIDER
#                           is cursor. Empty => visible failure.
#   PR_NUMBER               Pull request number (alternative to the argument).
#   GH_TOKEN                Token used by `gh` to fetch the PR diff.
#   GITHUB_TOKEN            Token used by lintro's `--post` to write comments.
#   GITHUB_REPOSITORY       owner/name; supplies --repo for `lintro review`.
#   GITHUB_RUN_ID           Current Actions run; excluded from prior-state lookup.
#   GITHUB_OUTPUT           When set, --locate-prior-state writes run-id=.
#   LINTRO_AI_ENABLED       Master switch; the workflow sets this to 1.
#   LINTRO_AI_PROVIDER      Optional overlay (workflow default: anthropic).
#   LINTRO_AI_MODEL         Optional overlay (empty = provider/config default).
#   LINTRO_AI_MAX_COST_USD  Optional spend ceiling overlay (empty = config default).
#   LINTRO_AI_TRANSPORT     Optional overlay (workflow default: cli).
#   LINTRO_REVIEW_STATE_DIR Directory for coverage artifacts (default:
#                           ai-review-state). The workflow downloads prior
#                           state here. This script uploads checkpoints
#                           and an inline snapshot so a cancelled job
#                           still leaves a resume artifact.
#   ACTIONS_RUNTIME_TOKEN   Present in Actions; required for in-step upload.
#   ACTIONS_RESULTS_URL     Actions artifact service origin for in-step upload.
#   GITHUB_RUN_ATTEMPT      Distinguishes artifact names across retries.
#   GITHUB_STEP_SUMMARY     When set, the outcome is appended as Markdown.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Dogfood `lintro review` on a pull request (informational, not required).

Usage:
  PR_NUMBER=<n> scripts/ci/run-ai-review.sh
  scripts/ci/run-ai-review.sh <pr-number>
  scripts/ci/run-ai-review.sh --locate-prior-state

Exits 0 only when a review was actually produced. A missing or dead credential,
a depleted balance, or an unreachable provider exits 1 with a visible reason.

--locate-prior-state lists completed trusted ai-review.yml runs and writes
run-id= for the latest one that carries a valid state artifact (empty when
none exist). When GITHUB_ACTIONS is set, it also seeds the immediately
older same-PR persist as part-0000-prior-* so coverage can union. Always
exits 0; missing state is a no-op, not a failure. Failures are retried
and logged to stderr.
EOF
	exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--locate-prior-state" ]]; then
	# Fail-safe: the locator never reddens the job. Empty run-id skips
	# download-artifact; the review then starts from empty coverage.
	exec python3 "${script_dir}/review_state_artifacts.py" locate
fi

# Sentinels understood by classify_review_outcome.py: the review was never
# invoked, either for want of a credential or for some other reason.
NO_CREDENTIAL_STATUS=-1
NOT_INVOKED_STATUS=-2

# Every "no review happened" path goes through the classifier rather than exiting
# directly, so each one produces the same annotation and job summary. An `exit 1`
# on its own reddens the check without saying why.
#
# python3, not `uv run`: the classifier is stdlib-only, so the visible-failure
# path must not depend on a synced virtualenv being present — which is exactly the
# kind of thing that breaks when a setup step is what failed.
report_not_invoked() {
	python3 "${script_dir}/classify_review_outcome.py" \
		--status "$NOT_INVOKED_STATUS" \
		--transport "$transport" \
		--reason "$1"
	exit 1
}

pr_number="${1:-${PR_NUMBER:-}}"

# bash-3.2-safe (macOS system bash): `${var,,}` is bash 4+ only (#2025).
provider="$(printf '%s' "${LINTRO_AI_PROVIDER:-anthropic}" | tr '[:upper:]' '[:lower:]')"
transport="$(printf '%s' "${LINTRO_AI_TRANSPORT:-cli}" | tr '[:upper:]' '[:lower:]')"
if [[ "$provider" == "cursor" ]]; then
	credential="${CURSOR_API_KEY:-}"
else
	credential="${CLAUDE_CODE_OAUTH_TOKEN:-}"
fi
if [[ -z "$credential" ]]; then
	exec python3 "${script_dir}/classify_review_outcome.py" \
		--status "$NO_CREDENTIAL_STATUS" \
		--transport "$transport"
fi

if [[ -z "$pr_number" ]]; then
	report_not_invoked "No PR number provided (set PR_NUMBER or pass it as an argument)."
fi

echo "Running AI review on PR #${pr_number} (posts comment)..."

# `--post` maintains the sticky review comment (and inline findings) on the PR.
# It needs GITHUB_TOKEN (write) and the repo; the diff is still fetched via `gh`.
repo_arg=()
if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
	repo_arg=(--repo "${GITHUB_REPOSITORY}")
fi

# Resume coverage is read from (and written to) this directory. The workflow
# downloads a prior run's artifact here. Incremental in-step uploads attach
# checkpoints so a cancelled job (which skips later always() steps) still
# leaves a resume artifact for the next round (#2166).
export LINTRO_REVIEW_STATE_DIR="${LINTRO_REVIEW_STATE_DIR:-ai-review-state}"
mkdir -p "${LINTRO_REVIEW_STATE_DIR}"

# Best-effort: never redden the review because an artifact upload failed.
# Keep the runtime token out of the agent CLI environment — lintro copies
# os.environ into the provider subprocess.
REVIEW_STATE_RUNTIME_TOKEN="${ACTIONS_RUNTIME_TOKEN:-}"
REVIEW_STATE_RESULTS_URL="${ACTIONS_RESULTS_URL:-}"
unset ACTIONS_RUNTIME_TOKEN ACTIONS_RESULTS_URL
# Post-wait inline upload must finish inside GitHub's ~7.5s SIGTERM
# grace so classify still runs. Mid-run checkpoints keep the longer cap.
_CANCEL_UPLOAD_BUDGET_SECONDS=2
_CHECKPOINT_UPLOAD_BUDGET_SECONDS=24
_upload_review_state() {
	local suffix="$1"
	local budget="${2:-$_CHECKPOINT_UPLOAD_BUDGET_SECONDS}"
	timeout --signal=TERM --kill-after=1 "$budget" \
		env ACTIONS_RUNTIME_TOKEN="${REVIEW_STATE_RUNTIME_TOKEN}" \
		ACTIONS_RESULTS_URL="${REVIEW_STATE_RESULTS_URL}" \
		python3 "${script_dir}/review_state_artifacts.py" \
		upload --suffix "$suffix" --budget-seconds "$budget" || true
}

# Heartbeat so a silent ``--output json`` review still proves the step is
# alive if the runner SIGTERM's it. Short sleeps so EXIT can reap quickly.
# When state.json changes, upload a checkpoint before the next SIGTERM.
(
	elapsed=0
	last_mtime=""
	while sleep 15; do
		elapsed=$((elapsed + 15))
		printf '[ai-review] still running (%ss)\n' "${elapsed}"
		mtime=$(stat -c %Y "${LINTRO_REVIEW_STATE_DIR}/state.json" 2>/dev/null || true)
		if [[ -n "$mtime" && "$mtime" != "$last_mtime" ]]; then
			last_mtime=$mtime
			_upload_review_state "ckpt-${elapsed}" "${_CHECKPOINT_UPLOAD_BUDGET_SECONDS}"
		fi
	done
) &
heartbeat_pid=$!

# Tee to a file: the classifier needs the JSON error envelope, and the live
# stream must still reach the Actions log so a mid-run SIGTERM is diagnosable.
output_file="$(mktemp)"
log_pid=""
lintro_pid=""
_cleanup_review() {
	rm -f "$output_file"
	kill -KILL "${heartbeat_pid:-}" 2>/dev/null || true
	kill -KILL "${log_pid:-}" 2>/dev/null || true
}
trap '_cleanup_review' EXIT
# Forward SIGTERM to lintro (runner may signal only this shell) and keep
# the log mirror alive so the JSON envelope still reaches $output_file.
_forward_term() {
	if [[ -n "${lintro_pid:-}" ]]; then
		kill -TERM "$lintro_pid" 2>/dev/null || true
		# uv run may wrap Python; signal non-session-leader children if uv
		# did not exec. Skip session leaders — that is the isolated agent
		# CLI (start_new_session). The orchestrator killpg's it on cancel.
		# TERMing the agent here can surface a non-timeout provider error
		# that aborts INCOMPLETE persist.
		while read -r child; do
			sid=$(ps -o sid= -p "$child" 2>/dev/null | tr -d ' ')
			if [[ -n "$sid" && "$sid" == "$child" ]]; then
				continue
			fi
			kill -TERM "$child" 2>/dev/null || true
		done < <(pgrep -P "$lintro_pid" || true)
	fi
}
trap '_forward_term' TERM INT

set +e
# Timeout comes from ai.transports.cli.timeout (default 1800s) — no hand-tuned
# --timeout at this call site (#1923). The default ai.api_timeout (60s) is
# sized for streaming API chunks; a CLI chunk is one agent invocation and
# needs minutes. Multi-chunk serial reviews (#2156 14-chunk dogfood) need
# the 1800s per-call budget so a ~20k-token chunk can finish.
#
# COUPLED to ai-review.yml's `timeout-minutes`: the resolved CLI timeout must
# fire BEFORE the Actions runner kills the job, or the review dies without a
# JSON envelope and classify_review_outcome.py reads a truncated file.
# Invariant (enforced by tests/scripts/test_run_ai_review.py):
# ceil(cli_timeout / 60) + setup overhead (~7 min) + posting margin
# < timeout-minutes. Bump both together.
#
# CLI_REVIEW_TIMEOUT_SECONDS documents the profile default the job budget
# must cover; tests/scripts/test_run_ai_review.py asserts it matches
# lintro.ai.transport.DEFAULT_CLI_TIMEOUT.
# shellcheck disable=SC2034  # documentation variable read by the wiring test
CLI_REVIEW_TIMEOUT_SECONDS=1800
echo "CLI timeout ${CLI_REVIEW_TIMEOUT_SECONDS}s; persist-on-SIGTERM enabled."
# Unbuffered Python. Write the envelope to a file (not a SIGTERM-fragile
# ``| tee`` pipe) and mirror it to the Actions log with a TERM-immune tail.
export PYTHONUNBUFFERED=1
uv run lintro review --pr "${pr_number}" "${repo_arg[@]}" --depth 1 --post --output json >"$output_file" 2>&1 &
lintro_pid=$!
# --pid makes tail exit when lintro is gone. SIGKILL reaps it if a
# group signal left it ignoring TERM (``trap '' TERM``).
(
	trap '' TERM
	tail --pid="$lintro_pid" -n +1 -f "$output_file"
) &
log_pid=$!
wait "$lintro_pid"
review_status=$?
# A trap can interrupt wait while lintro is still persisting. Reap it.
while kill -0 "$lintro_pid" 2>/dev/null; do
	wait "$lintro_pid"
	review_status=$?
done
# If wait returned 143 after lintro already exited, recover the real status.
if ! kill -0 "$lintro_pid" 2>/dev/null; then
	wait "$lintro_pid" 2>/dev/null
	reaped=$?
	if [[ "$reaped" -ne 127 ]]; then
		review_status=$reaped
	fi
fi
# Let GNU tail --pid flush the envelope, then SIGKILL the TERM-immune mirror.
for _ in 1 2 3 4; do
	kill -0 "${log_pid:-}" 2>/dev/null || break
	sleep 0.5
done
kill -KILL "${log_pid:-}" 2>/dev/null || true
wait "${log_pid:-}" 2>/dev/null || true
trap - TERM INT
set -e

# Upload before classify. GitHub cancels remaining always() steps after
# SIGTERM; this call still has the persist snapshot on disk (#2166).
# Bound to 2s so a hung Create/PUT/Finalize cannot eat classify.
_upload_review_state inline "${_CANCEL_UPLOAD_BUDGET_SECONDS}"

# Exits 0 only when a review was produced; the classifier writes the annotation
# and job summary either way. --transport names the failure vocabulary (#1923).
python3 "${script_dir}/classify_review_outcome.py" \
	--status "$review_status" \
	--transport "$transport" \
	--output-file "$output_file"
