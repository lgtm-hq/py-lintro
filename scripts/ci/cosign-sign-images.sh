#!/usr/bin/env bash
set -euo pipefail

# cosign-sign-images.sh
#
# Sign promoted image digests with Sigstore Cosign (keyless OIDC).
# Refs must be pinned by digest (image@sha256:...) so the signature is
# bound to the exact manifest CI validated and promoted (#1358) — never
# to a floating tag that could move between promotion and signing.

show_help() {
	cat <<'EOF'
Sign image digests with Cosign (keyless).

Usage:
  IMAGES=<refs> scripts/ci/cosign-sign-images.sh

Environment:
  IMAGES  Whitespace/newline-separated image refs pinned by digest,
          e.g. ghcr.io/lgtm-hq/py-lintro@sha256:... (required)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

images="${IMAGES:-}"

if [[ -z "$images" ]]; then
	echo "IMAGES is required" >&2
	exit 2
fi

refs=()
while IFS= read -r ref; do
	[[ -z "$ref" ]] && continue
	if [[ "$ref" != *@sha256:* ]]; then
		echo "Refusing to sign non-digest ref: ${ref}" >&2
		echo "(signatures must bind to a digest, not a floating tag)" >&2
		exit 2
	fi
	refs+=("$ref")
done <<<"$images"

if [[ ${#refs[@]} -eq 0 ]]; then
	echo "IMAGES did not contain any refs" >&2
	exit 2
fi

for ref in "${refs[@]}"; do
	echo "Signing ${ref}"
	cosign sign --yes "$ref"
done

echo "Signed ${#refs[@]} image(s)"
