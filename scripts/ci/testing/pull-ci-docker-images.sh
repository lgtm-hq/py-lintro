#!/usr/bin/env bash
set -euo pipefail

# pull-ci-docker-images.sh
#
# Pull ephemeral CI-tagged images from GHCR and retag them for local jobs.

show_help() {
	cat <<'EOF'
Pull CI-tagged lintro Docker images from GHCR.

Usage:
  CI_TAG=<tag> scripts/ci/testing/pull-ci-docker-images.sh {full|base|both}

Environment:
  CI_TAG            Ephemeral CI tag (required)
  GHCR_ORG_PACKAGE  Registry prefix (default: ghcr.io/lgtm-hq)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

mode="${1:-}"
ci_tag="${CI_TAG:-}"
registry="${GHCR_ORG_PACKAGE:-ghcr.io/lgtm-hq}"

if [[ -z "$mode" ]]; then
	echo "mode is required: full, base, or both" >&2
	exit 2
fi

if [[ -z "$ci_tag" ]]; then
	echo "CI_TAG is required" >&2
	exit 2
fi

pull_full() {
	docker pull "${registry}/py-lintro:${ci_tag}"
	docker tag "${registry}/py-lintro:${ci_tag}" py-lintro:latest
}

pull_base() {
	docker pull "${registry}/py-lintro-base:${ci_tag}"
	docker tag "${registry}/py-lintro-base:${ci_tag}" py-lintro:base
}

case "$mode" in
full)
	pull_full
	;;
base)
	pull_base
	;;
both)
	pull_full
	pull_base
	;;
*)
	echo "Invalid mode: ${mode} (expected full, base, or both)" >&2
	exit 2
	;;

esac
