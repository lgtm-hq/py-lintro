#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Resolve the installed vue-tsc version by reading its package.json from
# bun's global install root. vue-tsc's own `--version` flag prints the
# bundled TypeScript version, not vue-tsc's, so we have to look at the
# package metadata directly. bun (not npm) is the package manager in the
# tools image, so we cannot rely on `npm root -g`.

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve the installed vue-tsc version from bun's global package store.

Usage:
  scripts/ci/resolve-vue-tsc-version.sh

Searches (in order):
  $BUN_INSTALL/install/global/node_modules/vue-tsc/package.json
  $HOME/.bun/install/global/node_modules/vue-tsc/package.json

Exits 1 with a diagnostic if vue-tsc is not installed in either location.
EOF
	exit 0
fi

roots=()
if [[ -n "${BUN_INSTALL:-}" ]]; then
	roots+=("${BUN_INSTALL}/install/global/node_modules")
fi
if [[ -n "${HOME:-}" ]]; then
	roots+=("${HOME}/.bun/install/global/node_modules")
fi

for root in "${roots[@]}"; do
	pkg="${root}/vue-tsc/package.json"
	if [[ -f "$pkg" ]]; then
		python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$pkg"
		exit 0
	fi
done

echo "vue-tsc package.json not found in bun global install roots" >&2
exit 1
