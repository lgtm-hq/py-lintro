#!/usr/bin/env bash
set -euo pipefail

# Keep rolling default-branch image tags monotonic when queued workflow runs
# dispatch or finish out of order. Immutable sha-* tags are controlled by the
# workflow independently and remain enabled for every validated run.

show_help() {
	cat <<'EOF'
Decide whether this run may update rolling default-branch Docker tags.

Usage:
  DEFAULT_BRANCH=<branch> RUN_SHA=<sha> \
    scripts/ci/resolve-docker-rolling-tags.sh

Environment:
  DEFAULT_BRANCH  Repository default branch from the workflow event (required)
  RUN_SHA         Commit SHA validated by this workflow run (required)
  GITHUB_OUTPUT   When set, appends rolling-tags-enabled=true|false

The decision fails closed: an unavailable or malformed remote ref disables
rolling tags without failing the job, so immutable sha-* tags can still publish.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

default_branch="${DEFAULT_BRANCH:-}"
run_sha="${RUN_SHA:-}"

emit_decision() {
	local enabled="$1"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		echo "rolling-tags-enabled=${enabled}" >>"$GITHUB_OUTPUT"
	else
		echo "rolling-tags-enabled=${enabled}"
	fi
}

if [[ -z "$default_branch" || -z "$run_sha" ]]; then
	echo "::warning::Default branch or run SHA is unavailable; skipping rolling Docker tags"
	emit_decision false
	exit 0
fi

if ! git check-ref-format --branch "$default_branch" >/dev/null 2>&1 ||
	! [[ "$run_sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
	echo "::warning::Default branch or run SHA is malformed; skipping rolling Docker tags"
	emit_decision false
	exit 0
fi

remote_ref="refs/heads/${default_branch}"
if ! remote_result="$(git ls-remote --exit-code origin "$remote_ref" 2>/dev/null)"; then
	echo "::warning::Could not resolve origin/${default_branch}; skipping rolling Docker tags"
	emit_decision false
	exit 0
fi

remote_sha="$(awk -v ref="$remote_ref" '$2 == ref { print $1 }' <<<"$remote_result")"
if ! [[ "$remote_sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
	echo "::warning::origin/${default_branch} returned an invalid tip; skipping rolling Docker tags"
	emit_decision false
	exit 0
fi

if [[ "${run_sha,,}" == "${remote_sha,,}" ]]; then
	echo "Run ${run_sha} is the current origin/${default_branch} tip; enabling rolling Docker tags"
	emit_decision true
else
	echo "Run ${run_sha} is stale (origin/${default_branch} is ${remote_sha}); skipping rolling Docker tags"
	emit_decision false
fi
