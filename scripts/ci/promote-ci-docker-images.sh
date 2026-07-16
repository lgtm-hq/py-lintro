#!/usr/bin/env bash
set -euo pipefail

# promote-ci-docker-images.sh
#
# Promote the CI-validated image to release tags by digest instead of
# rebuilding (#1358). Resolves the digest behind the ephemeral CI tag,
# retags it with `docker buildx imagetools create`, then verifies every
# promoted tag resolves to that same digest — the published image is
# bit-identical to the image CI built, tested, and scanned.
#
# Promotion targets the digest (image@sha256:...), not the CI tag, so a
# concurrent run deleting the CI tag cannot race the promotion (#1138).
# Retagging is a registry-side manifest reference: the manifest (or
# manifest index, children included) is content-addressed and untouched,
# so attestation/signature children of an index are preserved.

show_help() {
	cat <<'EOF'
Promote a CI-tagged image to release tags by digest.

Usage:
  SOURCE_IMAGE=<image> CI_TAG=<tag> TAGS=<refs> \
    scripts/ci/promote-ci-docker-images.sh

Environment:
  SOURCE_IMAGE   Image repository, e.g. ghcr.io/lgtm-hq/py-lintro (required)
  CI_TAG         Ephemeral CI tag to promote, e.g. ci-123456789 (required)
  TAGS           Whitespace/newline-separated destination refs, e.g. the
                 docker/metadata-action tags output (required)
  GITHUB_OUTPUT  When set, `digest=<sha256:...>` is appended for
                 downstream steps (e.g. cosign signing)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

source_image="${SOURCE_IMAGE:-}"
ci_tag="${CI_TAG:-}"
tags="${TAGS:-}"

if [[ -z "$source_image" ]]; then
	echo "SOURCE_IMAGE is required" >&2
	exit 2
fi

if [[ -z "$ci_tag" ]]; then
	echo "CI_TAG is required" >&2
	exit 2
fi

if [[ -z "$tags" ]]; then
	echo "TAGS is required" >&2
	exit 2
fi

source_ref="${source_image}:${ci_tag}"

digest="$(docker buildx imagetools inspect \
	--format '{{.Manifest.Digest}}' "$source_ref")"

if [[ "$digest" != sha256:* ]]; then
	echo "Could not resolve digest for ${source_ref} (got: ${digest})" >&2
	exit 1
fi

echo "Promoting ${source_ref} (${digest})"

tag_args=()
tag_list=()
while IFS= read -r tag; do
	[[ -z "$tag" ]] && continue
	tag_args+=(--tag "$tag")
	tag_list+=("$tag")
done <<<"$tags"

if [[ ${#tag_list[@]} -eq 0 ]]; then
	echo "TAGS did not contain any destination refs" >&2
	exit 2
fi

# --prefer-index=false: with a single-manifest source (CI images are
# built with provenance disabled), the default prefer-index=true would
# wrap the manifest in a new one-entry index — a different digest. A
# carbon copy keeps the promoted tags on the exact CI-validated digest.
docker buildx imagetools create --prefer-index=false \
	"${source_image}@${digest}" "${tag_args[@]}"

# Verify the retag preserved the manifest: every promoted tag must resolve
# to the exact digest CI validated.
for tag in "${tag_list[@]}"; do
	promoted="$(docker buildx imagetools inspect \
		--format '{{.Manifest.Digest}}' "$tag")"
	if [[ "$promoted" != "$digest" ]]; then
		echo "Digest mismatch after promotion: ${tag} resolved to" \
			"${promoted}, expected ${digest}" >&2
		exit 1
	fi
	echo "Verified ${tag} -> ${promoted}"
done

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	echo "digest=${digest}" >>"$GITHUB_OUTPUT"
fi

echo "Promoted ${source_ref} to ${#tag_list[@]} tag(s) at ${digest}"
