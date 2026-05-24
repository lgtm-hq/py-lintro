#!/usr/bin/env bash
set -euo pipefail

# save-ci-images-tarball.sh
#
# Save locally loaded py-lintro Docker images to a tarball for artifact upload.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Save py-lintro Docker images to a tarball.

Usage:
  scripts/docker/save-ci-images-tarball.sh [output-path]

Arguments:
  output-path  Tarball path (default: /tmp/py-lintro-images.tar)
EOF
	exit 0
fi

output_path="${1:-/tmp/py-lintro-images.tar}"

docker save py-lintro:latest py-lintro:base -o "$output_path"
