#!/usr/bin/env bash
set -euo pipefail

# verify-image-attestations.sh
#
# Verify the GitHub build-provenance attestation of promoted image digests
# with `gh attestation verify oci://...` (#2562). Promotion is a registry-side
# retag, so the attestation the staging build pushed is bound to exactly the
# digest the version tags now carry; a digest without one must fail the
# release job rather than ship unverified.
#
# Refs must be pinned by digest (image@sha256:...) for the same reason the
# cosign step demands it: a floating tag could move between promotion and
# verification.

show_help() {
	cat <<'EOF'
Verify GitHub attestations on image digests.

Usage:
  IMAGES=<refs> ATTESTATION_REPO=<owner/repo> SIGNER_REPO=<owner/repo> \
    scripts/ci/verify-image-attestations.sh

Environment:
  IMAGES            Whitespace/newline-separated image refs pinned by digest,
                    e.g. ghcr.io/lgtm-hq/py-lintro@sha256:... (required)
  ATTESTATION_REPO  Repository whose attestations are consulted, passed as
                    `--repo` (required)
  SIGNER_REPO       Repository whose workflow signed the attestation, passed
                    as `--signer-repo`; the nested lgtm-ci reusable signs the
                    release images (required)
  GH_TOKEN          Token for the attestations API (required by gh)
  GITHUB_STEP_SUMMARY
                    When set, one line per verified digest is appended
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

images="${IMAGES:-}"
attestation_repo="${ATTESTATION_REPO:-}"
signer_repo="${SIGNER_REPO:-}"

if [[ -z "$images" ]]; then
	echo "IMAGES is required" >&2
	exit 2
fi
if [[ -z "$attestation_repo" ]]; then
	echo "ATTESTATION_REPO is required" >&2
	exit 2
fi
if [[ -z "$signer_repo" ]]; then
	echo "SIGNER_REPO is required" >&2
	exit 2
fi
if ! command -v gh >/dev/null 2>&1; then
	echo "gh not found; the GitHub CLI is required for attestation verification" >&2
	exit 2
fi

refs=()
while IFS= read -r ref; do
	[[ -z "$ref" ]] && continue
	if [[ "$ref" != *@sha256:* ]]; then
		echo "Refusing to verify non-digest ref: ${ref}" >&2
		echo "(verification must bind to a digest, not a floating tag)" >&2
		exit 2
	fi
	refs+=("$ref")
done <<<"$images"

if [[ ${#refs[@]} -eq 0 ]]; then
	echo "IMAGES did not contain any refs" >&2
	exit 2
fi

for ref in "${refs[@]}"; do
	echo "Verifying attestation for ${ref}"
	gh attestation verify "oci://${ref}" \
		--repo "$attestation_repo" \
		--signer-repo "$signer_repo"
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		echo "- attestation verified: \`${ref}\`" >>"$GITHUB_STEP_SUMMARY"
	fi
done

echo "Verified attestations for ${#refs[@]} image(s)"
