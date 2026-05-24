#!/usr/bin/env bash
set -euo pipefail

# detect-fork-pr.sh
#
# Detect whether the current GitHub Actions run is a fork pull request and write
# is-fork to GITHUB_OUTPUT for downstream job conditions.
#
# Usage:
#   EVENT_NAME=<name> IS_FORK_PR=<true|false> scripts/ci/detect-fork-pr.sh

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Detect fork pull requests for GitHub Actions.

Usage:
  EVENT_NAME=<event> IS_FORK_PR=<true|false> scripts/ci/detect-fork-pr.sh

Environment:
  EVENT_NAME   github.event_name
  IS_FORK_PR   github.event.pull_request.head.repo.fork

Outputs (via GITHUB_OUTPUT):
  is-fork=true|false
EOF
	exit 0
fi

event_name="${EVENT_NAME:-}"
is_fork_pr="${IS_FORK_PR:-false}"
is_fork="false"

if [[ "$event_name" == "pull_request" && "$is_fork_pr" == "true" ]]; then
	is_fork="true"
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	echo "is-fork=${is_fork}" >>"$GITHUB_OUTPUT"
else
	echo "is-fork=${is_fork}"
fi
