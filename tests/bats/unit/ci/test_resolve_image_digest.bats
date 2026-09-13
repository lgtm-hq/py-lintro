#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/resolve-image-digest.sh (#2602)

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/resolve-image-digest.sh"

COMMIT="1111111111111111111111111111111111111111"
RELEASE_COMMIT="2222222222222222222222222222222222222222"
COMMIT_DIGEST="sha256:aaaa111111111111111111111111111111111111111111111111111111111111"
LATEST_DIGEST="sha256:bbbb222222222222222222222222222222222222222222222222222222222222"

setup() {
	setup_temp_dir

	# Stubbed docker: the whole resolution is registry metadata, so a stub
	# that answers `imagetools inspect` for a declared set of refs exercises
	# every branch without a network or a daemon.
	#
	# RESOLVABLE  newline-separated "<ref> <digest>" pairs; anything else is
	#             a "not found" exit 1, exactly as imagetools reports it.
	# LABELS_JSON the inspect --format '{{json .}}' payload for any digest ref.
	DOCKER_STUB="${BATS_TEST_TMPDIR}/docker"
	cat >"${DOCKER_STUB}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
format=""
ref=""
for arg in "$@"; do
	case "${arg}" in
	'{{.Manifest.Digest}}') format="digest" ;;
	'{{json .}}') format="json" ;;
	*/*) ref="${arg}" ;;
	esac
done
if [[ "${format}" == "json" ]]; then
	printf '%s\n' "${LABELS_JSON}"
	exit 0
fi
while read -r candidate digest; do
	[[ -z "${candidate}" ]] && continue
	if [[ "${candidate}" == "${ref}" ]]; then
		printf '%s\n' "${digest}"
		exit 0
	fi
done <<<"${RESOLVABLE}"
echo "ERROR: ${ref}: not found" >&2
exit 1
EOF
	chmod +x "${DOCKER_STUB}"
	export DOCKER_BIN="${DOCKER_STUB}"

	export LABELS_JSON='{"image":{"config":{"Labels":{"org.opencontainers.image.version":"0.158.0","org.opencontainers.image.revision":"'"${RELEASE_COMMIT}"'"}}}}'
	export RESOLVABLE=""
	export GITHUB_OUTPUT="${BATS_TEST_TMPDIR}/outputs"
	export GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary"
	: >"${GITHUB_OUTPUT}"
	: >"${GITHUB_STEP_SUMMARY}"
}

teardown() {
	teardown_temp_dir
}

@test "resolve-image-digest.sh: --help exits 0" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "Resolve the dogfood image ref"
}

@test "resolve-image-digest.sh: requires a 40-hex COMMIT_SHA" {
	run env COMMIT_SHA="" "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"

	run env COMMIT_SHA="not-a-sha" "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
}

@test "resolve-image-digest.sh: prefers the per-commit image" {
	export RESOLVABLE="ghcr.io/lgtm-hq/py-lintro:sha-${COMMIT} ${COMMIT_DIGEST}
ghcr.io/lgtm-hq/py-lintro:latest ${LATEST_DIGEST}"

	run env COMMIT_SHA="${COMMIT}" "$SCRIPT"
	assert_success
	assert_output --partial "image=ghcr.io/lgtm-hq/py-lintro@${COMMIT_DIGEST}"
	assert_output --partial "source=commit"
	# The checkout already holds this commit's manifest, so that is what the
	# image gets verified against.
	assert_output --partial "manifest-ref=${COMMIT}"
}

@test "resolve-image-digest.sh: accepts the short per-commit tag" {
	# docker-ci's promote step asks for format=long while the release build
	# emits the short form; both must resolve or the preferred path is dead.
	export RESOLVABLE="ghcr.io/lgtm-hq/py-lintro:sha-${COMMIT:0:7} ${COMMIT_DIGEST}"

	run env COMMIT_SHA="${COMMIT}" "$SCRIPT"
	assert_success
	assert_output --partial "tag=sha-${COMMIT:0:7}"
	assert_output --partial "source=commit"
	assert_output --partial "manifest-ref=${COMMIT}"
}

@test "resolve-image-digest.sh: falls back to latest and its build commit" {
	export RESOLVABLE="ghcr.io/lgtm-hq/py-lintro:latest ${LATEST_DIGEST}"

	run env COMMIT_SHA="${COMMIT}" "$SCRIPT"
	assert_success
	assert_output --partial "image=ghcr.io/lgtm-hq/py-lintro@${LATEST_DIGEST}"
	assert_output --partial "version=0.158.0"
	assert_output --partial "source=fallback"
	# The whole point of #2602: a release image is compared against the
	# manifest of the commit that built it, never against main's.
	assert_output --partial "manifest-ref=${RELEASE_COMMIT}"
}

@test "resolve-image-digest.sh: reads labels from a multi-platform index" {
	export RESOLVABLE="ghcr.io/lgtm-hq/py-lintro:latest ${LATEST_DIGEST}"
	export LABELS_JSON='{"image":{"linux/amd64":{"config":{"Labels":{"org.opencontainers.image.version":"0.158.0","org.opencontainers.image.revision":"'"${RELEASE_COMMIT}"'"}}}}}'

	run env COMMIT_SHA="${COMMIT}" "$SCRIPT"
	assert_success
	assert_output --partial "version=0.158.0"
	assert_output --partial "manifest-ref=${RELEASE_COMMIT}"
}

@test "resolve-image-digest.sh: fails when nothing resolves" {
	export RESOLVABLE=""

	run env COMMIT_SHA="${COMMIT}" "$SCRIPT"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "Could not resolve ghcr.io/lgtm-hq/py-lintro:latest"
}

@test "resolve-image-digest.sh: fails when the fallback has no revision label" {
	export RESOLVABLE="ghcr.io/lgtm-hq/py-lintro:latest ${LATEST_DIGEST}"
	export LABELS_JSON='{"image":{"config":{"Labels":{"org.opencontainers.image.version":"0.158.0"}}}}'

	# Without a revision there is no manifest to compare against, and
	# guessing main's would recreate the every-night mismatch of #2602.
	run env COMMIT_SHA="${COMMIT}" "$SCRIPT"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "org.opencontainers.image.revision"
}

@test "resolve-image-digest.sh: publishes outputs and a job summary" {
	export RESOLVABLE="ghcr.io/lgtm-hq/py-lintro:sha-${COMMIT} ${COMMIT_DIGEST}"

	run env COMMIT_SHA="${COMMIT}" "$SCRIPT"
	assert_success

	run cat "${GITHUB_OUTPUT}"
	assert_output --partial "image=ghcr.io/lgtm-hq/py-lintro@${COMMIT_DIGEST}"
	assert_output --partial "manifest-ref=${COMMIT}"
	assert_output --partial "source=commit"

	# The resolved ref has to be visible on the run itself: the deduplicated
	# tracker issue renders a fixed body, so the summary is where triage
	# learns which image the night actually used.
	run cat "${GITHUB_STEP_SUMMARY}"
	assert_output --partial "Nightly dogfood image"
	assert_output --partial "ghcr.io/lgtm-hq/py-lintro@${COMMIT_DIGEST}"
	assert_output --partial "0.158.0"
}
