#!/usr/bin/env bash
# download_release_binaries.sh
# Download the three platform binaries and the SHA256SUMS manifest that the
# tag pipeline attaches to a GitHub release, laying them out for
# verify_release_binaries.sh and stage_binaries.py.

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 2 ]]; then
	cat <<'EOF'
Download lintro platform binaries and SHA256SUMS from a GitHub release.

Usage: download_release_binaries.sh <release-tag> <dest-dir>

Requires the GH_TOKEN environment variable (gh CLI auth). Places each
binary at <dest-dir>/<artifact-name>/<artifact-name> so that
stage_binaries.py can map them into the npm package tree, and the
release's SHA256SUMS manifest at <dest-dir>/SHA256SUMS so that
verify_release_binaries.sh can check every binary's digest before it is
staged. A release without SHA256SUMS fails the download.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

tag="$1"
dest="$2"

binaries=(
	"lintro-macos-arm64"
	"lintro-linux-arm64"
	"lintro-linux-x64"
)
checksums="SHA256SUMS"

mkdir -p "$dest"
for name in "${binaries[@]}"; do
	target_dir="$dest/$name"
	mkdir -p "$target_dir"
	echo "==> Downloading $name from release $tag"
	gh release download "$tag" --pattern "$name" --dir "$target_dir" --clobber
done

# The gate's manifest over every release asset (#2562). Pulled with the
# binaries so a digest check never has to trust a separately fetched file.
echo "==> Downloading $checksums from release $tag"
gh release download "$tag" --pattern "$checksums" --dir "$dest" --clobber
if [[ ! -s "$dest/$checksums" ]]; then
	echo "ERROR: release $tag has no $checksums asset; refusing to stage unverifiable binaries" >&2
	exit 1
fi

echo "Downloaded ${#binaries[@]} platform binaries and $checksums into $dest"
