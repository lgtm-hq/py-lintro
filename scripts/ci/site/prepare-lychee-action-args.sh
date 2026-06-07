#!/usr/bin/env bash
# Prepare lychee-action CLI args from lgtm-ci build-lychee-args output.
# SPDX-License-Identifier: MIT
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Filter lychee args and write apps/site/dist root-dir to GITHUB_OUTPUT."
	echo "Requires RAW_ARGS and GITHUB_OUTPUT environment variables."
	exit 0
fi

: "${RAW_ARGS:?RAW_ARGS is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

if [[ -n "${LYCHEE_ROOT_DIR:-}" ]]; then
	ROOT_DIR="${LYCHEE_ROOT_DIR}"
elif [[ -n "${GITHUB_WORKSPACE:-}" ]]; then
	ROOT_DIR="${GITHUB_WORKSPACE}/apps/site/dist"
else
	echo "prepare-lychee-action-args: set LYCHEE_ROOT_DIR or GITHUB_WORKSPACE" >&2
	exit 1
fi

read -r -a tokens <<<"$RAW_ARGS"
filtered=()
skip_next=false
for token in "${tokens[@]}"; do
	if [[ "$skip_next" == true ]]; then
		skip_next=false
		continue
	fi
	case "$token" in
	--format | --output) skip_next=true ;;
	--format=* | --output=*) ;;
	*) filtered+=("$token") ;;
	esac
done
filtered+=(--root-dir "${ROOT_DIR}")

delimiter="lychee_action_args_$(openssl rand -hex 16)"
{
	echo "args<<${delimiter}"
	printf '%s\n' "${filtered[*]}"
	echo "${delimiter}"
} >>"$GITHUB_OUTPUT"
