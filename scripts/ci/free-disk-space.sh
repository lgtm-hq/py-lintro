#!/usr/bin/env bash
set -euo pipefail

# free-disk-space.sh
#
# Reclaim disk space on GitHub-hosted runners before large Docker builds.
# Intended for fork PR fallback builds that cannot push to GHCR.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Reclaim disk space on GitHub-hosted runners for Docker builds.

Usage:
  scripts/ci/free-disk-space.sh
EOF
	exit 0
fi

if [[ "${GITHUB_ACTIONS:-}" != "true" && "${CI:-}" != "true" ]]; then
	echo "Skipping disk cleanup (not running in CI; set GITHUB_ACTIONS=true or CI=true to enable)"
	exit 0
fi

sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc \
	/usr/local/share/boost
sudo docker image prune -af
