#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Shared helper: resolve a pushed image's sha256 digest by pulling and
# inspecting RepoDigests. Sourced by tools-image-resolve.sh (which retries)
# and tools-image-resolve-digest.sh (single attempt) so retry + error
# behavior stay consistent across CI paths.
#
# Contract:
#   resolve_image_digest <image_tag> [max_attempts=1] [sleep_seconds=0]
#     Writes "<image_name>@<sha256>" to stdout on success (return 0).
#     On failure, writes a "::error::" line to stderr and returns 1.

# shellcheck shell=bash

resolve_image_digest() {
	local image_tag="$1"
	local max_attempts="${2:-1}"
	local sleep_seconds="${3:-0}"
	local image_name attempt repo_digests digest

	if [[ -z "$image_tag" ]]; then
		echo "::error::resolve_image_digest requires an image tag" >&2
		return 1
	fi

	image_name="${image_tag%:*}"
	for attempt in $(seq 1 "$max_attempts"); do
		echo "Resolving digest for ${image_tag} (attempt ${attempt}/${max_attempts})" >&2
		if docker pull "$image_tag" >/dev/null 2>&1; then
			repo_digests=$(docker inspect \
				--format='{{range .RepoDigests}}{{println .}}{{end}}' \
				"$image_tag" || true)
			digest=$(echo "$repo_digests" |
				awk -v name="$image_name" -F@ '$1==name {print $2; exit}')
			if [[ -n "$digest" ]]; then
				printf '%s@%s\n' "$image_name" "$digest"
				return 0
			fi
		fi
		if ((attempt < max_attempts)); then
			sleep "$sleep_seconds"
		fi
	done

	echo "::error::Unable to resolve a published digest for ${image_tag}" >&2
	return 1
}
