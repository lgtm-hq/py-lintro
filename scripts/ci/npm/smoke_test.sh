#!/usr/bin/env bash
# smoke_test.sh
# Verify a packed lintro meta-package resolves and launches its platform
# binary in a Python-free environment. Runs against locally packed tarballs
# so it works before anything is published to a registry.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
NPM_DIR="$REPO_ROOT/npm"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Smoke-test the lintro npm meta-package launcher.

Usage: smoke_test.sh

Detects the host platform, packs the meta-package and the matching
@lintro/<platform> package, installs them into a scratch project, and runs
`lintro --version`, asserting a zero exit code. Requires the platform binary
to already be staged into npm/<platform>/bin/lintro.
EOF
	exit 0
fi

node_platform="$(node -e 'process.stdout.write(process.platform)')"
node_arch="$(node -e 'process.stdout.write(process.arch)')"
platform_key="${node_platform}-${node_arch}"

platform_dir="$NPM_DIR/$platform_key"
if [[ ! -d "$platform_dir" ]]; then
	echo "Unsupported host platform for smoke test: $platform_key" >&2
	exit 1
fi

binary="$platform_dir/bin/lintro"
if [[ ! -x "$binary" ]]; then
	echo "Platform binary not staged/executable: $binary" >&2
	echo "Run scripts/ci/npm/stage_binaries.py first." >&2
	exit 1
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

meta_tarball="$(cd "$NPM_DIR/lintro" && npm pack --pack-destination "$workdir" | tail -n1)"
platform_tarball="$(cd "$platform_dir" && npm pack --pack-destination "$workdir" | tail -n1)"

cd "$workdir"
npm init -y >/dev/null 2>&1
npm install --no-save "$workdir/$platform_tarball" "$workdir/$meta_tarball"

echo "==> lintro --version"
./node_modules/.bin/lintro --version
echo "Smoke test passed: launcher resolved and executed the platform binary."
