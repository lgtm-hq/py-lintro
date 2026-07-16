#!/usr/bin/env bash
set -euo pipefail

# smoke-test-base-image.sh
#
# Verify the locally tagged py-lintro:base image runs lintro --version.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Smoke test the py-lintro:base Docker image.

Usage:
  scripts/docker/smoke-test-base-image.sh
EOF
	exit 0
fi

docker run --rm py-lintro:base lintro --version
