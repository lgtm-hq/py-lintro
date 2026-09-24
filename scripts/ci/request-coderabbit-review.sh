#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Ask CodeRabbit to review a bot-authored pull request (#2726).
#
# CodeRabbit skips pull requests opened by a bot ("Review skipped — Bot user
# detected") and ignores trigger comments posted by bot accounts, so the
# request has to come from a human account. The workflow hands this script the
# owner's fine-grained PAT (CODERABBIT_TRIGGER_TOKEN), and the script posts the
# one comment CodeRabbit answers to. Interim until CodeRabbit reviews
# App-authored pull requests; see docs/contributing.md for how to remove it.
#
# An empty token is a failure, not a skip: a missing secret must show up as a
# red job, because a quiet skip would leave every bot PR unreviewed unnoticed.
#
# Required environment:
#   GH_TOKEN   the owner's PAT (Pull requests: read and write); never printed
#   REPO       owner/name of the repository
#   PR_NUMBER  the pull request number

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Post `@coderabbitai review` on a pull request with a human account's token.

Usage:
  GH_TOKEN=... REPO=owner/name PR_NUMBER=123 \
    scripts/ci/request-coderabbit-review.sh

Environment:
  GH_TOKEN   Fine-grained PAT of a human account with Pull requests: read and
             write. Never printed. Empty is an error, not a skip.
  REPO       Repository as owner/name.
  PR_NUMBER  Pull request number.
EOF
	exit 0
fi

if [[ -z "${GH_TOKEN:-}" ]]; then
	echo "::error::GH_TOKEN is empty: the CODERABBIT_TRIGGER_TOKEN secret is not available to this job" >&2
	exit 1
fi
if [[ ! "${REPO:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
	echo "::error::REPO must be owner/name, got '${REPO:-}'" >&2
	exit 1
fi
if [[ ! "${PR_NUMBER:-}" =~ ^[1-9][0-9]*$ ]]; then
	echo "::error::PR_NUMBER must be a positive integer, got '${PR_NUMBER:-}'" >&2
	exit 1
fi

# The body is exactly the command CodeRabbit listens for. One request per push
# is the design; a manual rerun posts a second request for the same head,
# which at worst costs one repeat review. The workflow's per-PR concurrency
# group keeps rapid pushes from stacking requests.
if ! gh api "repos/${REPO}/issues/${PR_NUMBER}/comments" \
	-f body='@coderabbitai review' --silent; then
	echo "::error::could not post the CodeRabbit review request on ${REPO}#${PR_NUMBER}" >&2
	exit 1
fi
echo "Requested a CodeRabbit review on ${REPO}#${PR_NUMBER}"
