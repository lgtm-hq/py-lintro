#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/build/reuse_release_asset.sh (#2435)

load "../../helpers/common"

SCRIPT="${BUILD_SCRIPTS_DIR}/reuse_release_asset.sh"

# Minimal `gh` stub. Release assets live in $GH_STATE/assets/<name>, run
# artifacts in $GH_STATE/artifacts/<artifact>/<file>; anything absent makes the
# corresponding gh subcommand exit non-zero, which is what the script treats as
# "nothing to reuse".
write_gh_stub() {
	local dir="$1"
	mkdir -p "$dir"
	cat >"${dir}/gh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail

sub="${1:-} ${2:-}"
shift 2 || true

name=""
pattern=""
outdir=""
while [[ $# -gt 0 ]]; do
	case "$1" in
	--name)
		name="$2"
		shift 2
		;;
	--pattern)
		pattern="$2"
		shift 2
		;;
	--dir)
		outdir="$2"
		shift 2
		;;
	*)
		shift
		;;
	esac
done

case "$sub" in
"run download")
	src="${GH_STATE}/artifacts/${name}"
	[[ -d "$src" ]] || exit 1
	cp "$src"/* "$outdir/"
	;;
"release download")
	src="${GH_STATE}/assets/${pattern}"
	[[ -f "$src" ]] || exit 1
	cp "$src" "${outdir}/${pattern}"
	;;
*)
	echo "unsupported gh invocation: $sub" >&2
	exit 64
	;;
esac
STUB
	chmod +x "${dir}/gh"
}

setup() {
	setup_temp_dir
	setup_github_env
	WORKDIR="${BATS_TEST_TMPDIR}/work"
	mkdir -p "$WORKDIR"
	export GH_STATE="${BATS_TEST_TMPDIR}/gh-state"
	mkdir -p "${GH_STATE}/assets" "${GH_STATE}/artifacts"
	STUB_BIN="${BATS_TEST_TMPDIR}/stub-bin"
	write_gh_stub "$STUB_BIN"
	PATH="${STUB_BIN}:${PATH}"
	export PATH
	export GITHUB_REPOSITORY="lgtm-hq/py-lintro"
	export GITHUB_RUN_ID="12345"
	ASSET_NAME="lintro-linux-x64"
	ARTIFACT="sha256-linux-x64"
	DEST="${WORKDIR}/dist/nuitka/lintro-linux-x64"
}

teardown() {
	teardown_temp_dir
}

# Publish <content> as the release asset and record <checksum> in the run
# artifact. Passing a checksum that does not belong to the content is how the
# mismatch cases are built.
publish_asset() {
	printf '%s\n' "$1" >"${GH_STATE}/assets/${ASSET_NAME}"
}

publish_checksum_artifact() {
	mkdir -p "${GH_STATE}/artifacts/${ARTIFACT}"
	printf '%s\n' "$1" >"${GH_STATE}/artifacts/${ARTIFACT}/${ARTIFACT}.txt"
}

run_script() {
	# ${1-} not ${1:-}: an explicitly empty tag is a case under test.
	run "$SCRIPT" "${1-v1.2.3}" "$ASSET_NAME" "$ARTIFACT" "$DEST"
}

@test "reuse_release_asset.sh: --help exits 0 and documents the invariant" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "Decide whether a rerun can reuse"
	assert_output --partial "same-run"
}

@test "reuse_release_asset.sh: missing args exits 2" {
	run "$SCRIPT" v1.2.3 lintro-linux-x64
	assert_failure
	assert_equal "2" "$status"
}

@test "reuse_release_asset.sh: an empty release tag rebuilds" {
	run_script ""
	assert_success
	assert_equal "false" "$(get_github_output reuse)"
	assert_output --partial "No release tag resolved"
}

@test "reuse_release_asset.sh: no same-run checksum artifact rebuilds" {
	publish_asset "already-released-bytes"
	run_script
	assert_success
	assert_equal "false" "$(get_github_output reuse)"
	assert_output --partial "treating the release asset as unverified"
	[[ ! -f "$DEST" ]]
}

@test "reuse_release_asset.sh: no release asset rebuilds" {
	publish_checksum_artifact "$(printf '%064d' 0)"
	run_script
	assert_success
	assert_equal "false" "$(get_github_output reuse)"
	assert_output --partial "has no ${ASSET_NAME} asset"
}

@test "reuse_release_asset.sh: a checksum mismatch rebuilds and leaves no binary" {
	# The classic stale case: the release carries an asset from some other
	# build, so its bytes were never verified by this run.
	publish_asset "some-other-build"
	publish_checksum_artifact "$(printf '%064d' 1)"
	run_script
	assert_success
	assert_equal "false" "$(get_github_output reuse)"
	assert_output --partial "does not match the run checksum"
	[[ ! -f "$DEST" ]]
}

@test "reuse_release_asset.sh: a non-checksum artifact body rebuilds" {
	publish_asset "already-released-bytes"
	publish_checksum_artifact "not-a-sha256"
	run_script
	assert_success
	assert_equal "false" "$(get_github_output reuse)"
	assert_output --partial "does not hold a SHA256 digest"
}

@test "reuse_release_asset.sh: a verified asset is reused and staged executable" {
	publish_asset "already-released-bytes"
	local sha
	sha="$(compute_expected_sha256 "${GH_STATE}/assets/${ASSET_NAME}")"
	publish_checksum_artifact "$sha"

	run_script
	assert_success
	assert_equal "true" "$(get_github_output reuse)"
	assert_equal "$sha" "$(get_github_output sha256)"
	assert_output --partial "Reusing verified release asset"
	[[ -f "$DEST" ]]
	[[ -x "$DEST" ]]
	assert_equal "$sha" "$(compute_expected_sha256 "$DEST")"
}

@test "reuse_release_asset.sh: an uppercase checksum artifact still matches" {
	publish_asset "already-released-bytes"
	local sha upper
	sha="$(compute_expected_sha256 "${GH_STATE}/assets/${ASSET_NAME}")"
	upper="$(printf '%s' "$sha" | tr '[:lower:]' '[:upper:]')"
	publish_checksum_artifact "$upper"

	run_script
	assert_success
	assert_equal "true" "$(get_github_output reuse)"
	assert_equal "$sha" "$(get_github_output sha256)"
}

@test "reuse_release_asset.sh: a missing gh CLI rebuilds instead of failing" {
	stub_path="$(make_stub_path "${BATS_TEST_TMPDIR}/nogh" bash dirname cat mktemp)"
	run env PATH="$stub_path" GITHUB_OUTPUT="$GITHUB_OUTPUT" \
		"$SCRIPT" v1.2.3 "$ASSET_NAME" "$ARTIFACT" "$DEST"
	assert_success
	assert_equal "false" "$(get_github_output reuse)"
	assert_output --partial "gh CLI not available"
}

@test "reuse_release_asset.sh: succeeds without GITHUB_OUTPUT set" {
	publish_asset "already-released-bytes"
	publish_checksum_artifact "$(compute_expected_sha256 "${GH_STATE}/assets/${ASSET_NAME}")"
	local output_file="$GITHUB_OUTPUT"
	run env -u GITHUB_OUTPUT "$SCRIPT" v1.2.3 "$ASSET_NAME" "$ARTIFACT" "$DEST"
	assert_success
	assert_output --partial "Reusing verified release asset"
	assert_equal "" "$(cat "$output_file")"
}

@test "reuse_release_asset.sh: matches the Linux workflow argv contract" {
	# Same argv/env shape as the build-linux "Check for reusable release asset"
	# step; renaming an env var or mis-quoting an argument breaks this test.
	cd "$WORKDIR"
	publish_asset "already-released-bytes"
	publish_checksum_artifact "$(compute_expected_sha256 "${GH_STATE}/assets/${ASSET_NAME}")"
	export RELEASE_TAG=v1.2.3
	export BUILD_ARCH=x64

	run bash -c '"$0" "$RELEASE_TAG" "lintro-linux-$BUILD_ARCH" "sha256-linux-$BUILD_ARCH" "dist/nuitka/lintro-linux-$BUILD_ARCH"' "$SCRIPT"
	assert_success
	assert_equal "true" "$(get_github_output reuse)"
	[[ -x "dist/nuitka/lintro-linux-x64" ]]
}

@test "reuse_release_asset.sh: matches the macOS workflow argv contract" {
	cd "$WORKDIR"
	ASSET_NAME="lintro-macos-arm64"
	ARTIFACT="sha256-arm64"
	publish_asset "already-released-bytes"
	publish_checksum_artifact "$(compute_expected_sha256 "${GH_STATE}/assets/${ASSET_NAME}")"
	export RELEASE_TAG=v1.2.3
	export BUILD_ARCH=arm64

	run bash -c '"$0" "$RELEASE_TAG" "lintro-macos-$BUILD_ARCH" "sha256-$BUILD_ARCH" "dist/nuitka/lintro-macos-$BUILD_ARCH"' "$SCRIPT"
	assert_success
	assert_equal "true" "$(get_github_output reuse)"
	[[ -x "dist/nuitka/lintro-macos-arm64" ]]
}
