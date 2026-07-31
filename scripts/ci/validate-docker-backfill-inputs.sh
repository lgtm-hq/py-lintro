#!/usr/bin/env bash
set -euo pipefail

# Validate manual-dispatch inputs before either Docker image is published.
#
# Usage:
#   BACKFILL_VERSION=<version> BACKFILL_REF=<ref> FORCE_PUBLISH=<true|false> \
#     scripts/ci/validate-docker-backfill-inputs.sh

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Validate Docker backfill workflow-dispatch inputs.

Usage:
  BACKFILL_VERSION=<version> BACKFILL_REF=<ref> FORCE_PUBLISH=<true|false> \
    scripts/ci/validate-docker-backfill-inputs.sh

Environment:
  BACKFILL_VERSION  Version tag to publish, for example 0.65.0.
  BACKFILL_REF      Git ref containing that version, for example v0.65.0.
  FORCE_PUBLISH     Whether to force publishing; requires BACKFILL_VERSION.
EOF
	exit 0
fi

trim_input() {
	local value="$1"
	value="${value#"${value%%[![:space:]]*}"}"
	value="${value%"${value##*[![:space:]]}"}"
	printf '%s' "$value"
}

# Reject control characters (including newlines) so GITHUB_OUTPUT cannot be
# poisoned via multi-line workflow_dispatch inputs (output injection).
reject_control_chars() {
	local name="$1"
	local value="$2"
	if [[ "$value" == *$'\n'* || "$value" == *$'\r'* || "$value" =~ [[:cntrl:]] ]]; then
		echo "::error::${name} must not contain control characters or newlines." >&2
		exit 1
	fi
}

# Docker tags and git refs must not contain embedded whitespace after trim.
reject_embedded_whitespace() {
	local name="$1"
	local value="$2"
	if [[ "$value" =~ [[:space:]] ]]; then
		echo "::error::${name} must not contain whitespace." >&2
		exit 1
	fi
}

raw_backfill_version="${BACKFILL_VERSION:-}"
raw_backfill_ref="${BACKFILL_REF:-}"
raw_force_publish="${FORCE_PUBLISH:-false}"

reject_control_chars BACKFILL_VERSION "$raw_backfill_version"
reject_control_chars BACKFILL_REF "$raw_backfill_ref"
reject_control_chars FORCE_PUBLISH "$raw_force_publish"

backfill_version="$(trim_input "$raw_backfill_version")"
backfill_ref="$(trim_input "$raw_backfill_ref")"
force_publish="$(trim_input "$raw_force_publish")"

if [[ -n "$backfill_version" ]]; then
	reject_embedded_whitespace BACKFILL_VERSION "$backfill_version"
fi
if [[ -n "$backfill_ref" ]]; then
	reject_embedded_whitespace BACKFILL_REF "$backfill_ref"
fi

if [[ -n "$backfill_version" && -z "$backfill_ref" ]]; then
	echo "::error::BACKFILL_REF is required when BACKFILL_VERSION is set." >&2
	exit 1
fi

if [[ -n "$backfill_ref" && -z "$backfill_version" ]]; then
	echo "::error::BACKFILL_VERSION is required when BACKFILL_REF is set." >&2
	exit 1
fi

if [[ "$force_publish" == "true" && -z "$backfill_version" ]]; then
	echo "::error::FORCE_PUBLISH cannot be true when BACKFILL_VERSION is empty." >&2
	exit 1
fi

echo "Docker backfill inputs are valid."

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	{
		echo "backfill_version=${backfill_version}"
		echo "backfill_ref=${backfill_ref}"
		echo "force_publish=${force_publish}"
	} >>"$GITHUB_OUTPUT"
fi
