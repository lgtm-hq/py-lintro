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
# Lane binding (ADR-0011, #2870): the secret-free `lane` job of ai-review.yml
# resolves the chain from scripts/ci/ai_review_lanes.json and publishes the
# rows; this script binds one attempt to its lane. The lane job alone reads
# the two routing variables (LINTRO_AI_PROVIDER names a declared row,
# LINTRO_AI_MODEL overrides its default model) and resolves them into the
# row, so here provider, transport and model come from the row; the
# attempt sees exactly its
# own lane's credential, under the variable its CLI reads; the provider's
# base-URL override is set to the row's endpoint off-host and must be unset
# on-host; a competing credential, a config that disagrees with the row, or
# an absent credential refuses the run before any provider call.
#
# Trusted install: the workflow checks out github.workflow_sha, the
# base-branch commit that supplied the workflow and the manifest (the
# default branch on a comment run), before
# invoking this script, so the lintro that runs with the provider credential is
# trusted code — never the PR head. The PR diff is fetched independently by
# `lintro review --pr` via `gh` (GitHub API), so the PR's changes are reviewed
# as data and never executed with the token.
#
# Linter facts (#2571): this job runs no linter and no PR code. The untrusted
# docker-ci.yml lint job (pull_request, no secrets) lints the PR head and
# uploads lintro's JSON report as `linting-json-report`; this script locates
# the run whose head SHA equals the PR head, downloads that one artifact, and
# hands the file to `lintro review --lint-report`. The report is data: it is
# validated, restricted to the changed files, and fenced into the prompt like
# the diff. No report for this exact head (lint job still running, skipped,
# or expired) means the review proceeds without facts and says so in its
# header: "linter facts unavailable for this head".
#
# Fork PRs never reach this script: the workflow's job guard requires the head
# repo to be the base repo, so an empty credential means the secret is
# genuinely missing — a visible failure, not a skip.
#
# Usage:
#   PR_NUMBER=<n> LINTRO_REVIEW_CHAIN='["anthropic"]' \
#     LINTRO_REVIEW_LANES='{...}' CLAUDE_CODE_OAUTH_TOKEN=<token> GH_TOKEN=<token> \
#     scripts/ci/run-ai-review.sh
#   scripts/ci/run-ai-review.sh <pr-number>
#
# Environment:
#   LINTRO_REVIEW_CHAIN     JSON list of lane ids, active lane first, from the
#                           lane job (scripts/ci/resolve_review_lanes.py).
#   LINTRO_REVIEW_LANES     JSON object of the chain's rows, keyed by lane id.
#   LINTRO_REVIEW_ATTEMPT   Index into the chain for the attempt being bound;
#                           the wrapper walks the chain itself (#2872).
#   LINTRO_REVIEW_LANES_DOWN  Exported by the wrapper: JSON list of lanes that
#                           failed before any chunk completed, with the kind.
#   LINTRO_REVIEW_FALLBACK_FROM / _REASON  Exported for a fallback attempt so
#                           lintro's sticky leads with the dead lane.
#   <credential secrets>    Each chain credential under its SECRET name
#                           (CLAUDE_CODE_OAUTH_TOKEN, ZAI_AUTH_TOKEN,
#                           KIMI_API_KEY, CURSOR_API_KEY, CODEX_AUTH_JSON), gated by the
#                           workflow on chain membership. Captured, unset, and
#                           re-armed for the active lane only. Empty for the
#                           active lane => visible no-credential failure.
#   PR_NUMBER               Pull request number (alternative to the argument).
#   GH_TOKEN                Token used by `gh` to fetch the PR diff.
#   GITHUB_TOKEN            Token used by lintro's `--post` to write comments.
#                           GH_TOKEN also downloads the lint report artifact
#                           (`gh run download`, needs actions: read).
#   LINT_REPORT_POLL_SECONDS  Seconds between lint-report locator polls
#                           (default 30). Test override.
#   LINT_REPORT_WAIT_SECONDS  Upper bound on the lint-report wait before the
#                           review runs without linter facts (default 600).
#                           Test override; the default is pinned in tests.
#   GITHUB_REPOSITORY       owner/name; supplies --repo for `lintro review`.
#   GITHUB_RUN_ID           Current Actions run; excluded from prior-state lookup.
#   GITHUB_OUTPUT           When set, --locate-prior-state writes run-id=.
#   LINTRO_AI_ENABLED       Master switch; the workflow sets this to 1.
#   LINTRO_AI_PROVIDER      Set from the lane row; a value passed in is replaced.
#                           (The repository variable of the same name selects
#                           the lane, in the lane job, not here.)
#   LINTRO_AI_MODEL         Set from the lane row: the row's default, or the
#                           LINTRO_AI_MODEL repository variable the lane job
#                           resolved into it. Never empty.
#   LINTRO_AI_MAX_COST_USD  Optional spend ceiling overlay (empty = config default).
#   LINTRO_AI_REVIEW_PR_BUDGET_USD  Optional per-PR review budget overlay
#                           (#2796; empty = config default, unset = off).
#   LINTRO_AI_TRANSPORT     Set from the lane row.
#   LINTRO_EFFECTIVE_AI_JSON  The effective ai.* config as JSON, when the caller
#                           already read it (tests); otherwise read through
#                           `uv run lintro config --json`.
#   LINTRO_REVIEW_STATE_DIR Directory for coverage artifacts (default:
#                           ai-review-state). The workflow downloads prior
#                           state here. This script uploads checkpoints
#                           and an inline snapshot so a cancelled job
#                           still leaves a resume artifact.
#   ACTIONS_RUNTIME_TOKEN   Present in Actions; required for in-step upload.
#   ACTIONS_RESULTS_URL     Actions artifact service origin for in-step upload.
#   GITHUB_RUN_ATTEMPT      Distinguishes artifact names across retries.
#   GITHUB_STEP_SUMMARY     When set, the outcome is appended as Markdown.
#   REVIEW_REQUEST_MODE     On-request review (#2795): full, delta or paths,
#                           from the request job's validated output. Empty on
#                           pull-request events (unchanged behaviour). full
#                           adds --full; paths adds one --path per prefix.
#   REVIEW_REQUEST_PATHS    JSON list of path prefixes for mode paths,
#                           re-validated here before use.
#   REVIEW_REQUESTER        API-confirmed login of the requester; logged only.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Dogfood `lintro review` on a pull request (informational, not required).

Usage:
  PR_NUMBER=<n> scripts/ci/run-ai-review.sh
  scripts/ci/run-ai-review.sh <pr-number>
  scripts/ci/run-ai-review.sh --locate-prior-state

Exits 0 when a review was actually produced, or when an on-request review's
pull request is confirmed no longer eligible (closed, draft, or not from this
repository). Exits 1 with a visible reason otherwise: a missing or dead
credential, a depleted balance, an unreachable provider, an invalid request
input, or an on-request pull request that could not be read.

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
#
# LANE BINDING (ADR-0011 decisions A and D, #2870). The secret-free `lane`
# job resolved the chain from the committed manifest and published every
# chain row (LINTRO_REVIEW_LANES) and the order (LINTRO_REVIEW_CHAIN). This
# step holds every chain credential under its SECRET name and nothing else
# may route the run: the lane selector and the model override were applied
# by the lane job, and no variable here supplies a lane, transport or
# endpoint. Each attempt (LINTRO_REVIEW_ATTEMPT, index into the chain,
# walked below per decision D, #2872) sees exactly its own lane's
# credential, under the variable its CLI reads, and the row's facts.
if [[ -z "${LINTRO_REVIEW_CHAIN:-}" || -z "${LINTRO_REVIEW_LANES:-}" ]]; then
	transport="$(printf '%s' "${LINTRO_AI_TRANSPORT:-cli}" | tr '[:upper:]' '[:lower:]')"
	report_not_invoked "No resolved review lane: LINTRO_REVIEW_CHAIN and LINTRO_REVIEW_LANES are unset (the lane job did not publish them)."
fi

# Capture every credential a lane could bind into non-exported shell state
# and unset it, so the environment the CLI transport forwards to its
# subprocess holds one credential: the active lane's, re-armed below.
_LANE_CREDENTIAL_NAMES="CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_AUTH_TOKEN ZAI_AUTH_TOKEN KIMI_API_KEY CURSOR_API_KEY CODEX_AUTH_JSON"
for _name in $_LANE_CREDENTIAL_NAMES; do
	eval "_captured_${_name}=\"\${${_name}:-}\""
	unset "$_name"
done

# Export lines come from the stdlib gate script, one validated NAME=value
# per line; never `eval`'d as shell.
_apply_lane_exports() {
	local line name value
	while IFS= read -r line; do
		[[ -z "$line" ]] && continue
		name="${line%%=*}"
		value="${line#*=}"
		if [[ ! "$name" =~ ^[A-Z][A-Z0-9_]{0,127}$ ]]; then
			echo "::error::review lane: refusing export line with an invalid name" >&2
			exit 1
		fi
		printf -v "$name" '%s' "$value"
		# shellcheck disable=SC2163  # the name is validated above
		export "$name"
	done
}

# One attempt of the chain (ADR-0011 D, #2872): bind lane $1, re-arm only its
# credential, check it, materialise a file-form credential. The next attempt
# first takes back what this one exported (_unbind_attempt).
_bound_credential_env=""
_bound_base_url_env=""
_lane_credential_path=""
_lane_cleanup() {
	if [[ -n "$_lane_credential_path" ]]; then
		rm -f -- "$_lane_credential_path"
	fi
}
trap '_lane_cleanup' EXIT
_unbind_attempt() {
	_lane_cleanup
	_lane_credential_path=""
	if [[ -n "$_bound_credential_env" ]]; then
		unset "$_bound_credential_env"
		_bound_credential_env=""
	fi
	if [[ -n "$_bound_base_url_env" ]]; then
		unset "$_bound_base_url_env"
		_bound_base_url_env=""
	fi
}
# The lane id at index $1 of the chain, for messages about a lane whose
# binding failed before the gate could export it.
_lane_at() {
	printf '%s' "$LINTRO_REVIEW_CHAIN" | python3 -I -c '
import json, sys
value = json.load(sys.stdin)
index = int(sys.argv[1])
print(value[index] if isinstance(value, list) and 0 <= index < len(value) else "?")
' "$1" 2>/dev/null || echo "?"
}
# A binding refusal. On attempt 0 it is the whole run's verdict, as before:
# the no-credential status ($2) or a not-invoked report with the reason ($1).
# On a later attempt the review has already run and died on an earlier lane,
# so the refusal is reported and the walk goes on (the caller records the
# lane as unbound); the EXIT trap, the inline upload and the classify of the
# real outcome must all still happen.
_bind_refused() {
	if [[ "${_binding_attempt:-0}" != "0" ]]; then
		echo "::warning::lane $(_lane_at "$_binding_attempt") could not be bound: $1"
		return 1
	fi
	if [[ -n "${2:-}" ]]; then
		python3 "${script_dir}/classify_review_outcome.py" \
			--status "$2" \
			--transport "$transport"
		exit $?
	fi
	report_not_invoked "$1"
}
_bind_attempt() {
	export LINTRO_REVIEW_ATTEMPT="$1"
	_binding_attempt="$1"
	_lane_env_file="$(mktemp)"
	if ! python3 -I "${script_dir}/review_lane_env.py" --phase env >"$_lane_env_file"; then
		rm -f "$_lane_env_file"
		transport="$(printf '%s' "${LINTRO_AI_TRANSPORT:-cli}" | tr '[:upper:]' '[:lower:]')"
		_bind_refused "The resolved review lane could not be bound; see the error above." || return 1
	fi
	_apply_lane_exports <"$_lane_env_file"
	rm -f "$_lane_env_file"
	# Provider, transport and model now come from the row (ADR-0011 A).
	transport="$(printf '%s' "${LINTRO_AI_TRANSPORT:-cli}" | tr '[:upper:]' '[:lower:]')"
	provider="$(printf '%s' "${LINTRO_AI_PROVIDER:-}" | tr '[:upper:]' '[:lower:]')"
	echo "[ai-review] lane ${LINTRO_REVIEW_LANE:-?}: provider ${provider}, transport ${transport}, endpoint ${LINTRO_REVIEW_ENDPOINT:-?}"

	# Re-arm only the active lane's credential. An env-form credential is bound
	# under the variable its CLI reads (the z.ai token fills
	# ANTHROPIC_AUTH_TOKEN); a file-form one re-arms the source variable the
	# restore script decodes. Both come from the capture above, by name.
	# Indirect expansion, never eval: the names come from the manifest through
	# the gate, which validates them as variable names, and even a name that
	# slipped through could only be looked up, not executed.
	if [[ "${LANE_CREDENTIAL_FORM:-}" == "env" ]]; then
		_lane_capture_name="_captured_${LANE_CREDENTIAL_SECRET}"
		if [[ -n "${!_lane_capture_name:-}" ]]; then
			printf -v "$LANE_CREDENTIAL_ENV" '%s' "${!_lane_capture_name}"
			# shellcheck disable=SC2163  # a manifest name, validated by the gate
			export "$LANE_CREDENTIAL_ENV"
			_bound_credential_env="$LANE_CREDENTIAL_ENV"
		fi
		unset _lane_capture_name
	elif [[ "${LANE_CREDENTIAL_FORM:-}" == "file" ]]; then
		_lane_capture_name="_captured_${LANE_CREDENTIAL_SOURCE_ENV}"
		if [[ -n "${!_lane_capture_name:-}" ]]; then
			printf -v "$LANE_CREDENTIAL_SOURCE_ENV" '%s' "${!_lane_capture_name}"
			# shellcheck disable=SC2163  # a manifest name, validated by the gate
			export "$LANE_CREDENTIAL_SOURCE_ENV"
		fi
		unset _lane_capture_name
	fi
	# The provider's base-URL override is exported to the row's endpoint on a
	# non-first-party lane and left alone on a first-party one, where any set
	# value is a refusal below; nothing else sets it (ADR-0011 A).
	if [[ -n "${LANE_BASE_URL_ENV:-}" && -n "${LANE_BASE_URL_VALUE:-}" ]]; then
		printf -v "$LANE_BASE_URL_ENV" '%s' "$LANE_BASE_URL_VALUE"
		# shellcheck disable=SC2163  # a manifest name, validated by the gate
		export "$LANE_BASE_URL_ENV"
		_bound_base_url_env="$LANE_BASE_URL_ENV"
	fi

	# The committed config and its LINTRO_AI_* overlays are not a second source:
	# the effective ai.* must agree with the row. Read through lintro itself; in
	# Actions a config that cannot be read is a refusal, elsewhere (a developer
	# shell without the synced environment) the check is skipped with a note.
	# LINTRO_EFFECTIVE_AI_JSON, when already set outside Actions, is used as is
	# (the wrapper's tests supply it). In Actions it is ignored: the config is
	# read through lintro itself, so nothing set earlier in the job can stand
	# in for it.
	effective_ai_json="${LINTRO_EFFECTIVE_AI_JSON:-}"
	if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
		effective_ai_json=""
	fi
	if [[ -z "$effective_ai_json" ]] && command -v uv >/dev/null 2>&1; then
		effective_ai_json="$(uv run --no-sync lintro config --json 2>/dev/null | python3 -I -c '
import json, sys
value = json.load(sys.stdin)
print(json.dumps(value.get("ai", value) if isinstance(value, dict) else {}))
' 2>/dev/null || true)"
	fi
	if [[ -z "$effective_ai_json" ]]; then
		if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
			_bind_refused "The effective ai config could not be read (lintro config --json), so the lane binding cannot be verified." || return 1
		fi
		echo "[ai-review] effective-config check skipped: lintro config --json is unavailable here"
	fi

	# Refuse before any provider call: an absent credential is red with
	# status=no-credential, never a fallback trigger; a competing credential, a
	# base-URL override naming another host, or a config that disagrees with
	# the row is a refusal with its reason.
	# errexit is restored to what it was: attempt 0 binds under set -e, a
	# fallback attempt binds inside the loop, which runs under set +e so a
	# failed command there reaches classify instead of killing the script.
	_had_errexit=0
	[[ $- == *e* ]] && _had_errexit=1
	set +e
	LINTRO_EFFECTIVE_AI_JSON="$effective_ai_json" \
		python3 -I "${script_dir}/review_lane_env.py" --phase check
	_lane_check_status=$?
	[[ "$_had_errexit" == 1 ]] && set -e
	case "$_lane_check_status" in
	0) ;;
	2)
		_bind_refused "the lane's credential is absent" "$NO_CREDENTIAL_STATUS" || return 1
		;;
	*)
		_bind_refused "The resolved review lane refused to run; see the error above." || return 1
		;;
	esac

	# A file-form credential is materialised for the length of this attempt and
	# deleted at its end (ADR-0011 D). The restore script owns the decoding. A
	# file already at that path is somebody's session, not this attempt's: it is
	# neither overwritten nor deleted, and the attempt is refused.
	if [[ "${LANE_CREDENTIAL_FORM:-}" == "file" ]]; then
		if [[ -e "${LANE_CREDENTIAL_FILE//\$HOME/${HOME:-}}" ]]; then
			_bind_refused "The lane's credential file already exists at ${LANE_CREDENTIAL_FILE}; this attempt would overwrite a session it does not own." || return 1
		fi
		_lane_credential_path="${LANE_CREDENTIAL_FILE//\$HOME/${HOME:-}}"
		if ! "${script_dir}/restore-codex-session.sh"; then
			_bind_refused "The lane's file credential could not be materialised; see the error above." || return 1
		fi
		# The file is the credential now; the subprocess holds one copy of it.
		unset "$LANE_CREDENTIAL_SOURCE_ENV"
		if [[ ! -s "$_lane_credential_path" ]]; then
			_bind_refused "the lane's file credential is empty" "$NO_CREDENTIAL_STATUS" || return 1
		fi
	fi
	return 0
}
_bind_attempt 0

# One deduplicated tracker issue per dead lane, labelled ai-review-lane-down,
# through the minted App token (ADR-0011 D). Best-effort: it never reddens
# the review, and it never runs outside Actions.
_track_lane_down() {
	local lane="$1" kind="$2" title existing body
	if [[ -z "${GITHUB_TOKEN:-}" || -z "${GITHUB_REPOSITORY:-}" || -z "${GITHUB_ACTIONS:-}" ]]; then
		return 0
	fi
	title="AI review lane down: ${lane}"
	if [[ "$kind" == "unbound" ]]; then
		body="Lane \`${lane}\` could not be bound as a fallback lane (its credential is absent, or the gate refused it; see the run log) in run ${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID:-?} (PR #${pr_number:-?}). The walk went on to the next lane when one was left (ADR-0011 D). Close this issue once the lane's credential is configured."
	else
		body="Lane \`${lane}\` failed with \`${kind}\` before any chunk completed in run ${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID:-?} (PR #${pr_number:-?}). The walk went on to the next lane when one was left (ADR-0011 D). Close this issue once the lane's credential or balance is restored."
	fi
	(
		export GH_TOKEN="$GITHUB_TOKEN"
		gh label create ai-review-lane-down --repo "$GITHUB_REPOSITORY" --color B60205 \
			--description "A dogfood review lane failed before any chunk completed (ADR-0011 D)" \
			--force >/dev/null 2>&1 || true
		# The label list is exact and not index-delayed, unlike --search; the
		# title is compared whole so one lane id cannot match another's issue.
		existing="$(gh issue list --repo "$GITHUB_REPOSITORY" --label ai-review-lane-down \
			--state open --json number,title 2>/dev/null | python3 -I -c '
import json, sys
title = sys.argv[1]
try:
    issues = json.load(sys.stdin)
except ValueError:
    issues = []
for issue in issues if isinstance(issues, list) else []:
    if isinstance(issue, dict) and issue.get("title") == title:
        number = issue.get("number")
        if isinstance(number, int) and not isinstance(number, bool):
            print(number)
            break
' "$title" 2>/dev/null || true)"
		if [[ -n "$existing" ]]; then
			gh issue comment "$existing" --repo "$GITHUB_REPOSITORY" --body "$body" >/dev/null
		else
			gh issue create --repo "$GITHUB_REPOSITORY" --title "$title" \
				--label ai-review-lane-down --body "$body" >/dev/null
		fi
	) || echo "::warning::lane-down tracker could not be updated for lane ${lane}"
}

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

# On-request review (#2795). The request job already validated the mode and
# the prefixes; both are checked again here because this is the step that
# holds the credentials. Anything unexpected fails the job rather than
# silently widening or narrowing the review.
request_arg=()
case "${REVIEW_REQUEST_MODE:-}" in
"") ;;
full) request_arg=(--full) ;;
delta) ;;
paths)
	# Parse in a command substitution, not a process substitution, so a
	# malformed value fails here with its own message instead of looking like
	# an empty list.
	if ! request_paths=$(REVIEW_REQUEST_PATHS="${REVIEW_REQUEST_PATHS:-}" python3 -c '
import json, os, sys
value = json.loads(os.environ["REVIEW_REQUEST_PATHS"])
if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
    sys.exit(1)
sys.stdout.write("".join(f"{p}\n" for p in value))
' 2>/dev/null); then
		echo "::error::REVIEW_REQUEST_PATHS is not a JSON list of strings" >&2
		exit 1
	fi
	while IFS= read -r request_path; do
		[[ -z "$request_path" ]] && continue
		if [[ ! "$request_path" =~ ^[A-Za-z0-9._/-]{1,200}$ ||
			"$request_path" == /* || "$request_path" == -* ||
			"/${request_path}/" == */../* ]]; then
			echo "::error::refusing on-request path prefix '${request_path}'" >&2
			exit 1
		fi
		request_arg+=(--path "$request_path")
	done <<<"$request_paths"
	if [[ ${#request_arg[@]} -eq 0 ]]; then
		echo "::error::on-request paths review without any path prefix" >&2
		exit 1
	fi
	;;
*)
	echo "::error::unknown REVIEW_REQUEST_MODE '${REVIEW_REQUEST_MODE}'" >&2
	exit 1
	;;
esac
if [[ -n "${REVIEW_REQUEST_MODE:-}" ]]; then
	echo "[ai-review] on request by ${REVIEW_REQUESTER:-unknown}: ${REVIEW_REQUEST_MODE}"
	# The request job checked the PR, but this job may have waited in the
	# review-slot queue since. Re-check the same three conditions now. Two
	# outcomes are kept apart (#2806):
	#   - confirmed ineligible (closed, draft, or not from this repository):
	#     no review, exit 0 with a log line; there is nothing to review;
	#   - unreadable (gh failed, an empty or non-object reply, or an object
	#     without `state` or `head`): the request is
	#     NOT silently dropped; the job goes red with a visible reason so the
	#     writer sees it and can re-request.
	# The head itself may have moved: like a push review, the round reviews
	# the head as it is now (ruling 9 on #2795).
	# gh's own error goes to the job log, never into the reason: the reason
	# is a fixed sentence, and API text is not ours to put in an annotation.
	gh_stderr=$(mktemp)
	if ! pr_payload=$(gh api "repos/${GITHUB_REPOSITORY:-}/pulls/${pr_number}" 2>"$gh_stderr"); then
		echo "[ai-review] reading pull request #${pr_number} failed; gh said:" >&2
		cat "$gh_stderr" >&2
		rm -f "$gh_stderr"
		report_not_invoked "On-request review: pull request #${pr_number} could not be read after the queue wait; re-request with @lintro review."
	fi
	rm -f "$gh_stderr"
	pr_eligible=$(PR_PAYLOAD="$pr_payload" REPO="${GITHUB_REPOSITORY:-}" python3 -c '
import json, os
try:
    pull = json.loads(os.environ["PR_PAYLOAD"])
except ValueError:
    pull = None
if not isinstance(pull, dict) or "state" not in pull or "head" not in pull:
    print("unreadable")
else:
    head_repo = ((pull.get("head") or {}).get("repo") or {}).get("full_name", "")
    print("yes" if pull.get("state") == "open" and pull.get("draft") is False
          and head_repo == os.environ["REPO"] else "no")
' 2>/dev/null || echo unreadable)
	case "$pr_eligible" in
	yes) ;;
	no)
		echo "[ai-review] on-request review skipped: PR #${pr_number} is no longer open, non-draft and from ${GITHUB_REPOSITORY:-this repository}"
		# The workflow downloaded the prior round's state here; the always()
		# upload must not republish it as this skipped run's own state.
		rm -rf -- "${LINTRO_REVIEW_STATE_DIR:-ai-review-state}"
		exit 0
		;;
	*)
		report_not_invoked "On-request review: pull request #${pr_number} returned an unreadable reply after the queue wait; re-request with @lintro review."
		;;
	esac
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
# The upload never needs a provider credential, and the heartbeat that runs
# it forks after attempt 0 is bound: strip every lane credential name so no
# second process tree holds one (ADR-0011 D).
_credential_unset_args=()
for _name in $_LANE_CREDENTIAL_NAMES; do
	_credential_unset_args+=(-u "$_name")
done
unset _name
_upload_review_state() {
	local suffix="$1"
	local budget="${2:-$_CHECKPOINT_UPLOAD_BUDGET_SECONDS}"
	timeout --signal=TERM --kill-after=1 "$budget" \
		env "${_credential_unset_args[@]}" \
		ACTIONS_RUNTIME_TOKEN="${REVIEW_STATE_RUNTIME_TOKEN}" \
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
lint_report_dir="$(mktemp -d)"
_cleanup_review() {
	rm -f "$output_file"
	rm -rf "$lint_report_dir"
	kill -KILL "${heartbeat_pid:-}" 2>/dev/null || true
	kill -KILL "${log_pid:-}" 2>/dev/null || true
	_lane_cleanup
}
trap '_cleanup_review' EXIT
# Forward SIGTERM to lintro (runner may signal only this shell) and keep
# the log mirror alive so the JSON envelope still reaches $output_file.
_cancelled=""
_forward_term() {
	# Remembered for the chain loop: a fallback attempt is never started
	# after the runner asked this step to stop (ADR-0011 D).
	_cancelled=1
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
#
# The budget is per attempt. A chain walk (ADR-0011 D) happens only after a
# lane died before any chunk completed, which a dead credential or an
# exhausted account does on its first call, so a fallback attempt normally
# starts within seconds. A lane that takes most of the budget to fail that
# way is not covered: the fallback attempt may then be cut by the job
# timeout with no envelope, and the check reads as runner loss (#2296).
# shellcheck disable=SC2034  # documentation variable read by the wiring test
CLI_REVIEW_TIMEOUT_SECONDS=1800
echo "CLI timeout ${CLI_REVIEW_TIMEOUT_SECONDS}s; persist-on-SIGTERM enabled."

# Linter facts (#2571): fetch the untrusted lint job's JSON report for the
# exact PR head. The locator pins the run by head SHA (see
# review_state_artifacts.py lint-report); this script downloads only the run
# it named, and only that one artifact. Every failure path is fail-safe:
# without a report the review runs with no --lint-report flag at all, and
# --lint-report-missing carries the reason so lintro renders the single
# "linter facts unavailable for this head" note in
# the review header.
#
# Bounded wait: docker-ci.yml and ai-review.yml start on the same push, and
# the lint job needs minutes to upload its report, so a first-seconds lookup
# almost never finds it (#2583's dogfood run). The locator is polled every
# LINT_REPORT_POLL_SECONDS for up to LINT_REPORT_WAIT_SECONDS before the
# review falls back. Both are env-overridable so tests can shrink them; the
# defaults are pinned by tests/scripts/test_run_ai_review.py.
LINT_REPORT_POLL_SECONDS="${LINT_REPORT_POLL_SECONDS:-30}"
LINT_REPORT_WAIT_SECONDS="${LINT_REPORT_WAIT_SECONDS:-600}"
# Both must be whole seconds; the poll must be positive or the loop never
# advances. Anything else falls back to the default with a warning.
# Normalise through base 10 so a leading zero is not read as octal by the
# arithmetic below (0600 would otherwise become 384, and 08 an error).
if [[ "$LINT_REPORT_POLL_SECONDS" =~ ^[0-9]+$ ]] && [[ "$((10#$LINT_REPORT_POLL_SECONDS))" -ge 1 ]]; then
	LINT_REPORT_POLL_SECONDS=$((10#$LINT_REPORT_POLL_SECONDS))
else
	echo "::warning::LINT_REPORT_POLL_SECONDS=${LINT_REPORT_POLL_SECONDS} is not a positive integer; using 30"
	LINT_REPORT_POLL_SECONDS=30
fi
if [[ "$LINT_REPORT_WAIT_SECONDS" =~ ^[0-9]+$ ]]; then
	LINT_REPORT_WAIT_SECONDS=$((10#$LINT_REPORT_WAIT_SECONDS))
else
	echo "::warning::LINT_REPORT_WAIT_SECONDS=${LINT_REPORT_WAIT_SECONDS} is not a whole number of seconds; using 600"
	LINT_REPORT_WAIT_SECONDS=600
fi
lint_report_path="${lint_report_dir}/results.json"
lint_run_id=""
lint_head_sha=""
# The bound is wall clock, not summed sleeps: the locator's own gh calls
# count against it, so a slow API cannot stretch the wait past the budget
# the ai-review job books for it (timeout-minutes in ai-review.yml). At most
# one locate runs after the deadline, which the job budget allows for.
lint_wait_started=$SECONDS
lint_waited=0
while :; do
	if located="$(python3 "${script_dir}/review_state_artifacts.py" lint-report)"; then
		lint_run_id="$(printf '%s\n' "$located" | sed -n 's/^run-id=//p')"
		lint_head_sha="$(printf '%s\n' "$located" | sed -n 's/^head-sha=//p')"
	fi
	lint_waited=$((SECONDS - lint_wait_started))
	if [[ -n "$lint_run_id" || "$lint_waited" -ge "$LINT_REPORT_WAIT_SECONDS" ]]; then
		break
	fi
	# Never sleep past the bound: the last poll waits only the remainder.
	lint_sleep=$((LINT_REPORT_WAIT_SECONDS - lint_waited))
	if [[ "$lint_sleep" -gt "$LINT_REPORT_POLL_SECONDS" ]]; then
		lint_sleep="$LINT_REPORT_POLL_SECONDS"
	fi
	echo "[ai-review] linter facts: no linting-json-report yet for head ${lint_head_sha:-unknown}; retrying in ${lint_sleep}s (waited ${lint_waited}/${LINT_REPORT_WAIT_SECONDS}s)"
	sleep "$lint_sleep"
done
lint_report_arg=()
if [[ -n "$lint_run_id" ]]; then
	# Bounded: a stalled artifact download must not eat the review budget.
	if timeout --signal=TERM --kill-after=5 120 \
		gh run download "$lint_run_id" ${repo_arg[@]+"${repo_arg[@]}"} \
		--name linting-json-report --dir "$lint_report_dir" &&
		[[ -s "$lint_report_path" ]]; then
		lint_report_arg=(--lint-report "$lint_report_path")
		echo "[ai-review] linter facts: linting-json-report from docker-ci run ${lint_run_id} (head ${lint_head_sha}) after ${lint_waited}s"
	else
		echo "[ai-review] linter facts unavailable for this head: download of run ${lint_run_id} failed"
		lint_report_arg=(--lint-report-missing "download of docker-ci run ${lint_run_id} failed")
	fi
else
	echo "[ai-review] linter facts unavailable for this head: no linting-json-report for head ${lint_head_sha:-unknown} after ${lint_waited}s"
	lint_report_arg=(--lint-report-missing "no linting-json-report for head ${lint_head_sha:-unknown} after ${lint_waited}s")
fi
_run_review_attempt() {
	set +e
	# Unbuffered Python. Write the envelope to a file (not a SIGTERM-fragile
	# ``| tee`` pipe) and mirror it to the Actions log with a TERM-immune tail.
	export PYTHONUNBUFFERED=1
	uv run lintro review --pr "${pr_number}" ${repo_arg[@]+"${repo_arg[@]}"} --depth 1 --post ${request_arg[@]+"${request_arg[@]}"} ${lint_report_arg[@]+"${lint_report_arg[@]}"} --output json >"$output_file" 2>&1 &
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
}

# The chain walk (ADR-0011 D, #2872). After each attempt the stdlib decider
# says whether to advance: only the review error status, only the
# dead-credential or exhausted-account kinds, only when no chunk of the
# attempt completed (the envelope's completed_chunks is zero and no
# checkpoint part is newer than the attempt's marker), and only while a
# lane is left. A dead lane is recorded for the classifier
# (LINTRO_REVIEW_LANES_DOWN), tracked in one issue, and named to lintro
# for the sticky's fallback line (LINTRO_REVIEW_FALLBACK_FROM). A fallback
# lane that cannot be bound (no credential, a gate refusal) is recorded as
# `unbound` and the walk goes on; a cancel ends the walk at once.
_chain_length="$(printf '%s' "$LINTRO_REVIEW_CHAIN" | python3 -I -c '
import json, sys
value = json.load(sys.stdin)
print(len(value) if isinstance(value, list) else 0)
' 2>/dev/null || echo 0)"
_attempt=0
export LINTRO_REVIEW_LANES_DOWN='[]'
# Record a lane that failed before any chunk completed: on the list the
# classifier reads, and in the lane-down tracker issue.
_record_lane_down() {
	local _lane="$1" _kind="$2" _next_down
	if _next_down="$(python3 -I "${script_dir}/review_chain.py" lanes-down \
		--current "$LINTRO_REVIEW_LANES_DOWN" --lane "$_lane" --kind "$_kind")"; then
		export LINTRO_REVIEW_LANES_DOWN="$_next_down"
	fi
	_track_lane_down "$_lane" "$_kind"
}

while :; do
	_attempt_marker="$(mktemp)"
	_run_review_attempt
	_decision="$(python3 -I "${script_dir}/review_chain.py" decide \
		--status "$review_status" --output-file "$output_file" \
		--state-dir "$LINTRO_REVIEW_STATE_DIR" --since "$_attempt_marker" \
		--attempt "$_attempt" --chain-length "$_chain_length" 2>/dev/null || echo "stop decider failed")"
	rm -f "$_attempt_marker"
	if [[ -n "$_cancelled" && "$_decision" == advance* ]]; then
		_decision="stop cancelled during the attempt; no fallback attempt is started"
	fi
	case "$_decision" in
	advance\ *)
		_dead_kind="${_decision#advance }"
		_dead_lane="${LINTRO_REVIEW_LANE:-?}"
		echo "::warning::lane ${_dead_lane} unavailable (${_dead_kind}) before any chunk completed; falling back to the next lane (ADR-0011 D)"
		# Take the dead lane's credential back before anything else runs.
		_unbind_attempt
		_record_lane_down "$_dead_lane" "$_dead_kind"
		# The sticky names the primary lane: the first one that died, not
		# whichever preceded the lane that finally answered.
		if [[ -z "${LINTRO_REVIEW_FALLBACK_FROM:-}" ]]; then
			export LINTRO_REVIEW_FALLBACK_FROM="$_dead_lane" LINTRO_REVIEW_FALLBACK_REASON="$_dead_kind"
		fi
		# Bind the next lane. One that cannot be bound is a dead lane too:
		# it is recorded as unbound and the next is tried, until a lane
		# binds or none is left. The last real attempt's status and output
		# stand for the classifier either way.
		while :; do
			if [[ -n "$_cancelled" ]]; then
				echo "[ai-review] chain: cancelled; no fallback attempt is started"
				break 2
			fi
			_attempt=$((_attempt + 1))
			if _bind_attempt "$_attempt"; then
				rm -f "$output_file"
				output_file="$(mktemp)"
				break
			fi
			_unbound_lane="$(_lane_at "$_attempt")"
			_unbind_attempt
			_record_lane_down "$_unbound_lane" unbound
			if ((_attempt + 1 >= _chain_length)); then
				echo "[ai-review] chain: lane ${_unbound_lane} could not be bound; no lane left"
				break 2
			fi
		done
		;;
	exhausted\ *)
		# Dead before any chunk, but the last lane: record it like the
		# others so the classifier can tell "every lane dead" from "the
		# last lane died mid-review", then stop.
		_dead_kind="${_decision#exhausted }"
		_dead_lane="${LINTRO_REVIEW_LANE:-?}"
		echo "[ai-review] chain: lane ${_dead_lane} unavailable (${_dead_kind}) before any chunk completed; no lane left"
		_record_lane_down "$_dead_lane" "$_dead_kind"
		break
		;;
	*)
		echo "[ai-review] chain: ${_decision}"
		break
		;;
	esac
done
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
