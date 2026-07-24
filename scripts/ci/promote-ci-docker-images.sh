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

# Bounded exponential-backoff retry for the registry read/retag calls. A
# single transient CDN blip ("connection reset by peer", "httpReadSeeker
# failed") from GitHub's registry otherwise fails the whole main-push
# promote (#1696). Overridable so tests can drive them fast.
PROMOTE_MAX_ATTEMPTS="${PROMOTE_MAX_ATTEMPTS:-4}"
PROMOTE_BACKOFF_SECONDS="${PROMOTE_BACKOFF_SECONDS:-2}"

if ! [[ "$PROMOTE_MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
	echo "PROMOTE_MAX_ATTEMPTS must be a positive integer (got: ${PROMOTE_MAX_ATTEMPTS})" >&2
	exit 2
fi
if ! [[ "$PROMOTE_BACKOFF_SECONDS" =~ ^[0-9]+$ ]]; then
	echo "PROMOTE_BACKOFF_SECONDS must be a non-negative integer (got: ${PROMOTE_BACKOFF_SECONDS})" >&2
	exit 2
fi

# Transient network/registry errors worth retrying. A genuine auth/denied/
# manifest-unknown failure is deliberately NOT here: those are permanent and
# must fail on the first attempt rather than be retried away (#1696).
_promote_transient_re='connection reset by peer|httpReadSeeker|broken pipe|i/o timeout|TLS handshake|unexpected EOF|temporarily unavailable|toomanyrequests|50[0234] (Internal Server Error|Bad Gateway|Service Unavailable|Gateway Time-out)'

# retry_registry <cmd...>
#
# Run a registry command, retrying only on a transient signature. Command
# stdout is captured and re-emitted verbatim on success so callers can still
# `digest="$(retry_registry docker ... inspect ...)"`. stderr is streamed
# through on every attempt so progress/errors stay visible in the job log.
retry_registry() {
	local attempt=1 delay="$PROMOTE_BACKOFF_SECONDS" out rc err
	err="$(mktemp)"
	# Clean up explicitly at each return rather than via a RETURN trap, which
	# would overwrite/leak into the caller's shell trap context.
	while true; do
		if out="$("$@" 2>"$err")"; then
			cat "$err" >&2
			rm -f "$err"
			[[ -n "$out" ]] && printf '%s\n' "$out"
			return 0
		else
			# Capture the failed command's status here: a bodyless `if`
			# that falls through would leave $? as the `if`'s own 0.
			rc=$?
		fi
		cat "$err" >&2
		if ((attempt >= PROMOTE_MAX_ATTEMPTS)) ||
			! grep -qiE "$_promote_transient_re" "$err"; then
			rm -f "$err"
			return "$rc"
		fi
		echo "Transient registry error on '$1' (attempt ${attempt}/${PROMOTE_MAX_ATTEMPTS}); retrying in ${delay}s..." >&2
		sleep "$delay"
		delay=$((delay * 2))
		attempt=$((attempt + 1))
	done
}

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

digest="$(retry_registry docker buildx imagetools inspect \
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
retry_registry docker buildx imagetools create --prefer-index=false \
	"${source_image}@${digest}" "${tag_args[@]}"

# Verify the retag preserved the manifest: every promoted tag must resolve
# to the exact digest CI validated.
for tag in "${tag_list[@]}"; do
	promoted="$(retry_registry docker buildx imagetools inspect \
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
