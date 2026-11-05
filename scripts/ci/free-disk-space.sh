#!/usr/bin/env bash
set -euo pipefail

# free-disk-space.sh
#
# Reclaim disk space on GitHub-hosted runners before large Docker builds.
# Runs before every hosted image build in docker-ci, fork or not (#3085).
# Idempotent: `rm -rf` of an already-removed path succeeds, so reruns and
# repeated calls are safe.
#
# Only preinstalled toolchains the image-build job never uses are removed;
# the Python and Node entries under /opt/hostedtoolcache are left alone.
# Runs only when RUNNER_ENVIRONMENT is github-hosted: a self-hosted runner's
# toolchains belong to the host, so the script skips with a notice there.

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

if [[ "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ]]; then
	echo "::notice::Skipping disk cleanup (RUNNER_ENVIRONMENT='${RUNNER_ENVIRONMENT:-}' is not github-hosted; host toolchains are left alone)"
	exit 0
fi

echo "Disk usage before cleanup:"
df -h /

sudo rm -rf \
	/usr/local/lib/android \
	/usr/share/dotnet \
	/opt/ghc \
	/usr/local/.ghcup \
	/usr/local/share/boost \
	/usr/share/swift \
	/usr/local/share/powershell \
	/opt/hostedtoolcache/CodeQL
sudo docker image prune -af

echo "Disk usage after cleanup:"
df -h /
