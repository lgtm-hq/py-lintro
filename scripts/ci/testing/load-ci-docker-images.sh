#!/usr/bin/env bash
set -euo pipefail

# load-ci-docker-images.sh
#
# Load py-lintro Docker images from a tarball produced by save-ci-images-tarball.sh.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Load py-lintro Docker images from a tarball.

Retags py-lintro:latest as py-lintro-test:latest (matches pull-ci-docker-images.sh).

Usage:
  scripts/ci/testing/load-ci-docker-images.sh [tarball-path]

Arguments:
  tarball-path  Input tarball (default: /tmp/py-lintro-images.tar)
EOF
	exit 0
fi

tarball_path="${1:-/tmp/py-lintro-images.tar}"

docker load -i "$tarball_path"
docker tag py-lintro:latest py-lintro-test:latest
