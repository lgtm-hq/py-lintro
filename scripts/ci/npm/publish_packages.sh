#!/usr/bin/env bash
# publish_packages.sh
# Publish the lintro npm packages (platform packages first, then the
# meta-package). Publishing is DRY-RUN ONLY unless LIVE=1 is set explicitly,
# which is intentionally never wired into any workflow in this repo — going
# live is a deliberate, separate follow-up that also requires NODE_AUTH_TOKEN.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
NPM_DIR="$REPO_ROOT/npm"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Publish lintro npm packages.

Usage: publish_packages.sh

Environment:
  LIVE=1              Perform a real publish. Default (unset) is --dry-run.
  NPM_PROVENANCE=0    Disable --provenance (default: enabled).

Publishes @lgtm-hq/lintro-<platform> packages first, then the root meta-package,
so consumers never resolve a meta-package whose optional deps are missing.
EOF
	exit 0
fi

# Platform packages before the meta-package: the meta-package's
# optionalDependencies must exist on the registry first.
PACKAGES=(
	"darwin-arm64"
	"darwin-x64"
	"linux-arm64"
	"linux-x64"
	"lintro"
)

publish_flags=("--access" "public")
if [[ "${NPM_PROVENANCE:-1}" != "0" ]]; then
	publish_flags+=("--provenance")
fi
if [[ "${LIVE:-0}" != "1" ]]; then
	publish_flags+=("--dry-run")
	echo "DRY-RUN mode: no packages will be published. Set LIVE=1 to publish."
else
	echo "LIVE mode: packages WILL be published to the registry."
fi

for pkg in "${PACKAGES[@]}"; do
	pkg_dir="$NPM_DIR/$pkg"
	echo "==> Publishing $pkg (${publish_flags[*]})"
	(cd "$pkg_dir" && npm publish "${publish_flags[@]}")
done

echo "npm publish step complete."
