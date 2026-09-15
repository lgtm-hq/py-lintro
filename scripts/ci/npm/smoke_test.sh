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
@lgtm-hq/lintro-<platform> package, installs them into a scratch project, and runs
`lintro --version`, asserting a zero exit code. Requires the platform binary
to already be staged into npm/<platform>/bin/lintro.

The run is repeated with the installed platform binary stripped of its
executable bit (mode 0644): that is how the binary reaches the publish job,
because the artifact handoff between jobs drops file modes, so the launcher
must restore it before anything is published.
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

# The publish job receives the staged set as a workflow artifact, whose zip
# lands every file as 0644, so the published binary may lack +x. Prove the
# launcher repairs that (npm/lintro/lib/resolve.js ensureExecutable) here,
# before the set is attested and handed over.
installed_binary="./node_modules/@lgtm-hq/lintro-${platform_key}/bin/lintro"
if [[ ! -f "$installed_binary" ]]; then
	echo "Installed platform binary not found: $installed_binary" >&2
	exit 1
fi
chmod 0644 "$installed_binary"
echo "==> lintro --version (platform binary stripped of its exec bit)"
./node_modules/.bin/lintro --version
if [[ ! -x "$installed_binary" ]]; then
	echo "Launcher ran but did not restore the exec bit on $installed_binary" >&2
	exit 1
fi
echo "Smoke test passed: launcher resolved and executed the platform binary, with and without its exec bit."
