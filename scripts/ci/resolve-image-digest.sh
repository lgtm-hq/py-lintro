#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# resolve-image-digest.sh
#
# Resolve, at run time, the py-lintro image the nightly dogfood run should
# lint with — and the git ref whose tool manifest that image must be compared
# against (#2602).
#
# The nightly used to carry a hand-maintained `py-lintro:<version>@sha256:...`
# pin at five sites. Nothing moved it automatically (the Renovate docker
# datasource cannot page past thousands of `sha-<commit>` tags, and the release
# sync script reaches CI without a token, lgtm-hq/lgtm-ci#849), so the pin
# froze at 0.148.0 while main moved to 0.156.x. Verifying a frozen image
# against main's CURRENT manifest then failed every single night on tool
# version mismatches, and a permanently red nightly hides real regressions.
#
# Resolution order — first hit wins:
#   1. `<repo>:sha-<COMMIT_SHA>`, then `<repo>:sha-<short sha>`  the image
#      docker-ci promoted for the exact commit this run checked out. Both tag
#      shapes are tried because the two publishers disagree: docker-ci's
#      promote step asks docker/metadata-action for `format=long` while the
#      release build emits the default short form. Coherent by construction:
#      image and manifest come from the same tree.
#   2. `<repo>:<FALLBACK_TAG>` (default `latest`)  the newest published
#      release. docker-ci only promotes `sha-` tags for main pushes that ran
#      the docker pipeline, so a nightly on a docs-only HEAD has no per-commit
#      image. The fallback stays coherent a different way: the manifest ref is
#      read back from the image's own `org.opencontainers.image.revision`
#      label, so the comparison is against the commit that BUILT the image,
#      not against main.
#
# Both paths therefore satisfy the invariant the old pin broke: the image
# under test and the manifest it is checked against always describe the same
# commit. A genuine manifest-vs-image drift still fails loudly; a stale pin
# can no longer manufacture one.
#
# Fail-loud, not fail-open: an unresolvable image, or a fallback image whose
# revision label cannot be read, exits non-zero. A night with no coverage must
# reach the tracker, never pass silently.
#
# Usage:
#   COMMIT_SHA=$GITHUB_SHA scripts/ci/resolve-image-digest.sh

show_help() {
	cat <<'EOF'
Resolve the dogfood image ref and the manifest ref to compare it against.

Usage:
  COMMIT_SHA=<40-hex sha> scripts/ci/resolve-image-digest.sh

Environment:
  COMMIT_SHA    Required. Commit this run checked out (github.sha).
  IMAGE_REPO    Optional. Image repository
                (default: ghcr.io/lgtm-hq/py-lintro).
  COMMIT_TAG_PREFIX  Optional. Per-commit tag prefix (default: sha-).
  FALLBACK_TAG  Optional. Tag used when no per-commit image exists
                (default: latest).
  DOCKER_BIN    Optional. Docker executable (default: docker). Tests point
                this at a stub.
  GITHUB_OUTPUT Optional. When set, the outputs below are appended to it.
  GITHUB_STEP_SUMMARY  Optional. When set, a summary block is appended.

Outputs (stdout, and appended to GITHUB_OUTPUT when set):
  image         Fully resolved ref, always <repo>@sha256:<digest>
  version       Release version of the resolved image, or "unknown"
  manifest-ref  Git ref whose manifest the image must be verified against
  source        commit | fallback
  tag           The tag the digest was resolved through

Exit codes:
  0  an image and a manifest ref were resolved
  1  no image could be resolved, or the fallback carried no revision label
  2  usage error
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

commit_sha="${COMMIT_SHA:-}"
image_repo="${IMAGE_REPO:-ghcr.io/lgtm-hq/py-lintro}"
commit_tag_prefix="${COMMIT_TAG_PREFIX:-sha-}"
fallback_tag="${FALLBACK_TAG:-latest}"
docker_bin="${DOCKER_BIN:-docker}"

log_info() { echo "[INFO] $*"; }
log_warn() { echo "::warning::$*"; }
log_error() { echo "[ERROR] $*" >&2; }

if [[ -z "$commit_sha" ]]; then
	log_error "COMMIT_SHA is required (e.g. \${{ github.sha }})"
	exit 2
fi
if ! [[ "$commit_sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
	log_error "COMMIT_SHA must be a 40-character commit sha (got: ${commit_sha})"
	exit 2
fi
if [[ -z "$image_repo" || -z "$fallback_tag" ]]; then
	log_error "IMAGE_REPO and FALLBACK_TAG must be non-empty"
	exit 2
fi

# resolve_digest <ref> — print the manifest digest a tag resolves to.
resolve_digest() {
	local ref="$1" digest
	digest="$("$docker_bin" buildx imagetools inspect \
		--format '{{.Manifest.Digest}}' "$ref" 2>/dev/null)" || return 1
	[[ "$digest" == sha256:* ]] || return 1
	printf '%s\n' "$digest"
}

# image_label <ref> <label> — print an OCI label from the image config.
#
# Multi-platform refs expose `.image` as a platform->image map; single-manifest
# refs expose it as the image itself. Both shapes carry the same labels, so the
# first entry is representative. A missing label prints nothing.
image_label() {
	local ref="$1" label="$2"
	"$docker_bin" buildx imagetools inspect --format '{{json .}}' "$ref" \
		2>/dev/null |
		jq -r --arg label "$label" '
			(.image // {}) as $image
			| (if ($image | type) == "object" and ($image | has("config"))
				then $image
				else ($image | to_entries | map(.value) | first // {})
				end)
			| ((.config // {}).Labels // {})[$label] // ""
		' 2>/dev/null || true
}

commit_sha_lower="$(printf '%s' "$commit_sha" | tr '[:upper:]' '[:lower:]')"
commit_tags=(
	"${commit_tag_prefix}${commit_sha_lower}"
	"${commit_tag_prefix}${commit_sha_lower:0:7}"
)
source=""
tag=""
digest=""

for candidate in "${commit_tags[@]}"; do
	if digest="$(resolve_digest "${image_repo}:${candidate}")"; then
		source="commit"
		tag="$candidate"
		log_info "Resolved the per-commit image for ${commit_sha} via ${candidate}"
		break
	fi
done

if [[ -z "$source" ]]; then
	log_warn "No per-commit image for ${commit_sha} (tried: ${commit_tags[*]}); falling back to :${fallback_tag}"
	if ! digest="$(resolve_digest "${image_repo}:${fallback_tag}")"; then
		log_error "Could not resolve ${image_repo}:${fallback_tag}"
		log_error "The nightly has no image to lint with; failing loudly rather than skipping coverage."
		exit 1
	fi
	source="fallback"
	tag="$fallback_tag"
fi

image="${image_repo}@${digest}"
version="$(image_label "$image" org.opencontainers.image.version)"
revision="$(image_label "$image" org.opencontainers.image.revision)"
[[ -n "$version" ]] || version="unknown"

if [[ "$source" == "commit" ]]; then
	# The image was promoted for this very commit, so the checkout already
	# holds the manifest it must be verified against.
	manifest_ref="$commit_sha"
else
	# Verify against the manifest of the commit that built the fallback image,
	# never against main's — comparing a release image with main's manifest is
	# exactly the mismatch #2602 exists to stop.
	if ! [[ "$revision" =~ ^[0-9a-fA-F]{40}$ ]]; then
		log_error "${image} carries no usable org.opencontainers.image.revision label (got: '${revision}')"
		log_error "Without it the manifest to verify against is unknown, and a mismatch would be meaningless."
		exit 1
	fi
	manifest_ref="$revision"
fi

log_info "Image:        ${image}"
log_info "Tag:          ${tag} (${source})"
log_info "Version:      ${version}"
log_info "Manifest ref: ${manifest_ref}"

outputs=(
	"image=${image}"
	"version=${version}"
	"manifest-ref=${manifest_ref}"
	"source=${source}"
	"tag=${tag}"
)
for line in "${outputs[@]}"; do
	printf '%s\n' "$line"
done
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	for line in "${outputs[@]}"; do
		printf '%s\n' "$line" >>"$GITHUB_OUTPUT"
	done
fi

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
	{
		echo "### Nightly dogfood image"
		echo
		echo "- Image: \`${image}\`"
		echo "- Version: \`${version}\`"
		echo "- Resolved through: \`${tag}\` (${source})"
		echo "- Manifest verified at: \`${manifest_ref}\`"
		if [[ "$source" != "commit" ]]; then
			echo
			echo "No per-commit image existed for \`${commit_sha}\`, so the"
			echo "newest published release was used and its own build commit"
			echo "supplies the manifest under comparison."
		fi
	} >>"$GITHUB_STEP_SUMMARY"
fi
