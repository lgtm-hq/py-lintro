#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# tag-pinned-tools-digest.sh
#
# Attach pinned-<merge-sha7> to the lintro-tools digest currently pinned in
# the consumer Dockerfiles. Does not move :latest (#2845).

show_help() {
	cat <<'EOF'
Tag the Dockerfile-pinned lintro-tools digest as pinned-<sha7>.

Usage:
  GITHUB_SHA=<merge sha> scripts/ci/tag-pinned-tools-digest.sh

Environment:
  GITHUB_SHA     Merge commit whose first seven hex chars name the tag
  SOURCE_IMAGE   Image repository (default: ghcr.io/lgtm-hq/lintro-tools)
  PIN_READER     Override the digest reader (tests)
  REQUIRE_EPHEMERAL_ONLY
                 When "true", skip tagging if the digest already has a
                 persistent tag (one-time backfill, #2845)
  GH_TOKEN       Required when REQUIRE_EPHEMERAL_ONLY=true
  GITHUB_REPOSITORY  owner/name used to resolve the GHCR org
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

if [[ -z "${GITHUB_SHA:-}" ]]; then
	echo "GITHUB_SHA is required" >&2
	exit 2
fi

_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_reader="${PIN_READER:-${_script_dir}/update-tools-image-digest.py}"
digest="$(python3 "$_reader" --read)"
if [[ "$digest" != sha256:* ]]; then
	echo "Could not read pinned lintro-tools digest (got: ${digest})" >&2
	exit 1
fi

sha7="$(printf '%s' "$GITHUB_SHA" | tr '[:upper:]' '[:lower:]')"
sha7="${sha7:0:7}"
source_image="${SOURCE_IMAGE:-ghcr.io/lgtm-hq/lintro-tools}"

if [[ "${REQUIRE_EPHEMERAL_ONLY:-false}" == "true" ]]; then
	needs_rc=0
	DIGEST="$digest" python3 "${_script_dir}/pinned-digest-needs-tag.py" || needs_rc=$?
	if [[ "$needs_rc" -eq 1 ]]; then
		echo "Skipping pinned-${sha7}: ${digest} already has a persistent tag"
		exit 0
	fi
	if [[ "$needs_rc" -ne 0 ]]; then
		exit "$needs_rc"
	fi
fi

SOURCE_IMAGE="$source_image" \
	SOURCE_DIGEST="$digest" \
	EXPECTED_DIGEST="$digest" \
	TAGS="${source_image}:pinned-${sha7}" \
	"${_script_dir}/promote-ci-docker-images.sh"
