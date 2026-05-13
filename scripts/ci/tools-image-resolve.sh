#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Resolve the tools image tag based on context.
# Called from ci-pipeline.yml resolve-tools job and resolve-tools-image composite action.
#
# Resolution order:
#   1. BUILT_IMAGE                    (set when ci-pipeline.yml called tools-image.yml via workflow_call)
#   2. IMAGE_NAME:sha-${GITHUB_SHA}@digest (push events with tool changes)
#   3. STABLE_IMAGE                   (PRs without tool changes and pushes without tool changes)
#      If STABLE_IMAGE is not provided, reads the pinned value from Dockerfile.
#
# Required environment variables:
#   GITHUB_EVENT_NAME   - "push", "pull_request", or "workflow_dispatch"
#   IMAGE_NAME          - Base image name (e.g., ghcr.io/lgtm-hq/lintro-tools)
#   GITHUB_OUTPUT       - Path to GitHub outputs file
#
# Optional environment variables:
#   CALL_RESULT         - Result of the call-tools-image job ("success", "skipped", etc.)
#                         When "success", TOOLS_CHANGED is true, and PR_NUMBER is set,
#                         derives the built PR image tag.
#   PR_NUMBER           - PR number; used with CALL_RESULT to derive the PR image tag.
#   BUILT_IMAGE         - Pre-built image override (takes priority over CALL_RESULT if set)
#   STABLE_IMAGE        - Full stable image reference with digest; read from Dockerfile if unset
#   TOOLS_CHANGED       - "true"/"false" to decide whether a fresh build/image must be used
#   GITHUB_SHA          - Commit SHA for push/main resolution
#   IS_FORK_PR          - "true" when the pull request comes from a fork (uses artifact fallback)

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve the tools image tag based on context.

Usage:
  scripts/ci/tools-image-resolve.sh

Resolution order:
  1. BUILT_IMAGE                 — pre-built image from workflow_call (highest priority)
  2. IMAGE_NAME:sha-${GITHUB_SHA}@digest — push events with tool changes
  3. STABLE_IMAGE                — PRs without tool changes (read from Dockerfile if unset)

Environment Variables (required):
  GITHUB_EVENT_NAME   GitHub event name (push, pull_request, workflow_dispatch, ...)
  IMAGE_NAME          Base image name (e.g., ghcr.io/lgtm-hq/lintro-tools)
  GITHUB_OUTPUT       Path to GitHub output file

Environment Variables (optional):
  BUILT_IMAGE         Full image reference from a workflow_call build; if set, used directly
  STABLE_IMAGE        Full stable image reference with digest; read from Dockerfile if unset
  TOOLS_CHANGED       "true"/"false" — gates BUILT_IMAGE derivation from CALL_RESULT/PR_NUMBER
                      and chooses between fresh-digest resolution and STABLE_IMAGE on push

Outputs (to GITHUB_OUTPUT):
  image               Full image reference to use
  source              Where the image comes from: registry, artifact, or stable

Example (fresh build from workflow_call via CALL_RESULT):
  CALL_RESULT=success PR_NUMBER=123 \
  GITHUB_EVENT_NAME=pull_request IMAGE_NAME=ghcr.io/org/lintro-tools \
  GITHUB_OUTPUT=/tmp/out ./scripts/ci/tools-image-resolve.sh

Example (stable image for PR without tool changes):
  GITHUB_EVENT_NAME=pull_request IMAGE_NAME=ghcr.io/org/lintro-tools \
  GITHUB_OUTPUT=/tmp/out ./scripts/ci/tools-image-resolve.sh
EOF
	exit 0
fi

: "${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME is required}"
: "${IMAGE_NAME:?IMAGE_NAME is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

get_tools_image_from_dockerfile() {
	# Echo the pinned TOOLS_IMAGE value from Dockerfile or an empty string
	# if the file or ARG line is missing. Keeps error output quiet so callers
	# can decide how to surface the failure.
	grep -E '^ARG TOOLS_IMAGE=' Dockerfile 2>/dev/null | head -n1 | cut -d= -f2 || true
}

# shellcheck disable=SC1091 # helper path resolved at runtime via $(dirname "$0")
source "$(dirname "$0")/tools-image-digest-helpers.sh"

# Derive BUILT_IMAGE from CALL_RESULT + PR_NUMBER when not set directly.
# This avoids referencing reusable-workflow job outputs (which causes startup_failure
# when the calling job has a conditional if:).
if [[ -z "${BUILT_IMAGE:-}" && "${CALL_RESULT:-}" == "success" &&
	"${TOOLS_CHANGED:-false}" == "true" && -n "${PR_NUMBER:-}" ]]; then
	BUILT_IMAGE="${IMAGE_NAME}:pr-${PR_NUMBER}"
fi

# ------------------------------------------------------------------
# 1. Pre-built image from workflow_call (highest priority)
# ------------------------------------------------------------------
if [[ -n "${BUILT_IMAGE:-}" ]]; then
	IMAGE="$BUILT_IMAGE"
	if [[ "${IS_FORK_PR:-false}" == "true" ]]; then
		SOURCE="artifact"
		echo "Using pre-built tools image artifact from workflow_call: ${IMAGE}"
	else
		SOURCE="registry"
		echo "Using pre-built tools image from workflow_call: ${IMAGE}"
	fi

# ------------------------------------------------------------------
# 2. Push to main — require the commit-scoped SHA tag when tool files changed
# ------------------------------------------------------------------
elif [[ "$GITHUB_EVENT_NAME" == "push" ]]; then
	CHANGED="${TOOLS_CHANGED:-false}"
	if [[ "$CHANGED" == "true" ]]; then
		: "${GITHUB_SHA:?GITHUB_SHA is required when TOOLS_CHANGED=true on push}"
		IMAGE_TAG="${IMAGE_NAME}:sha-${GITHUB_SHA}"
		# Poll until GHCR serves the commit-scoped tag (tools-image.yml may still
		# be publishing). Budget ~8.5m sleeps + pulls; ci job timeout must exceed this.
		IMAGE=$(resolve_image_digest "$IMAGE_TAG" 35 15)
		SOURCE="registry"
		echo "Using commit-scoped tools image digest for push: ${IMAGE}"
	else
		if [[ -z "${STABLE_IMAGE:-}" ]]; then
			STABLE_IMAGE=$(get_tools_image_from_dockerfile)
		fi
		if [[ -z "${STABLE_IMAGE:-}" ]]; then
			echo "::error::STABLE_IMAGE is unset and could not be read from Dockerfile"
			exit 1
		fi
		IMAGE="${STABLE_IMAGE}"
		SOURCE="stable"
		echo "Using stable tools image (no tool changes on push): ${IMAGE}"
	fi

# ------------------------------------------------------------------
# 3. Stable pinned image — PRs without tool changes, workflow_dispatch, etc.
# ------------------------------------------------------------------
else
	if [[ -z "${STABLE_IMAGE:-}" ]]; then
		# Read the pinned digest directly from Dockerfile — kept current by pin-digest job
		STABLE_IMAGE=$(get_tools_image_from_dockerfile)
	fi
	if [[ -z "${STABLE_IMAGE:-}" ]]; then
		echo "::error::STABLE_IMAGE is unset and could not be read from Dockerfile"
		exit 1
	fi
	IMAGE="${STABLE_IMAGE}"
	SOURCE="stable"
	echo "Using stable tools image (pinned digest): ${IMAGE}"
fi

echo "image=${IMAGE}" >>"$GITHUB_OUTPUT"
echo "source=${SOURCE}" >>"$GITHUB_OUTPUT"
