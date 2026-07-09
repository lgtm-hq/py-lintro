#!/usr/bin/env bash
# download_release_binaries.sh
# Download the four platform binaries that build-binary.yml attaches to a
# GitHub release, laying them out for stage_binaries.py.

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 2 ]]; then
	cat <<'EOF'
Download lintro platform binaries from a GitHub release.

Usage: download_release_binaries.sh <release-tag> <dest-dir>

Requires the GH_TOKEN environment variable (gh CLI auth). Places each
binary at <dest-dir>/<artifact-name>/<artifact-name> so that
stage_binaries.py can map them into the npm package tree.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

tag="$1"
dest="$2"

binaries=(
	"lintro-macos-arm64"
	"lintro-macos-x86_64"
	"lintro-linux-arm64"
	"lintro-linux-x64"
)

mkdir -p "$dest"
for name in "${binaries[@]}"; do
	target_dir="$dest/$name"
	mkdir -p "$target_dir"
	echo "==> Downloading $name from release $tag"
	gh release download "$tag" --pattern "$name" --dir "$target_dir" --clobber
done

echo "Downloaded ${#binaries[@]} platform binaries into $dest"
