#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/build/upload_release_asset.sh (#2435)

load "../../helpers/common"

SCRIPT="${BUILD_SCRIPTS_DIR}/upload_release_asset.sh"

# `gh` stub backed by a directory of release assets. Asset ids are the asset
# names, which is enough to model the delete-by-id and rename-by-id calls the
# script makes. GH_STUB_CORRUPT_UPLOAD makes the "server" store different bytes
# than were uploaded, exercising the post-upload verification.
write_gh_stub() {
	local dir="$1"
	mkdir -p "$dir"
	cat >"${dir}/gh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail

assets="${GH_STATE}/assets"
mkdir -p "$assets"

case "${1:-} ${2:-}" in
"release upload")
	file="$4"
	name="$(basename "$file")"
	[[ -e "${assets}/${name}" ]] && {
		echo "asset already exists: $name" >&2
		exit 1
	}
	cp "$file" "${assets}/${name}"
	if [[ -n "${GH_STUB_CORRUPT_UPLOAD:-}" ]]; then
		printf 'truncated' >"${assets}/${name}"
	fi
	;;
"release download")
	shift 2
	pattern=""
	outdir=""
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--pattern)
			pattern="$2"
			shift 2
			;;
		--dir)
			outdir="$2"
			shift 2
			;;
		*) shift ;;
		esac
	done
	[[ -f "${assets}/${pattern}" ]] || exit 1
	cp "${assets}/${pattern}" "${outdir}/${pattern}"
	;;
"api -X")
	method="$3"
	path="$4"
	id="${path##*/}"
	case "$method" in
	DELETE)
		rm -f "${assets:?}/${id}"
		echo "delete ${id}" >>"$GH_STUB_LOG"
		;;
	PATCH)
		new_name=""
		shift 4
		while [[ $# -gt 0 ]]; do
			case "$1" in
			-f)
				new_name="${2#name=}"
				shift 2
				;;
			*) shift ;;
			esac
		done
		[[ -n "$new_name" ]] || exit 1
		[[ -f "${assets}/${id}" ]] || exit 1
		mv "${assets}/${id}" "${assets}/${new_name}"
		echo "rename ${id} -> ${new_name}" >>"$GH_STUB_LOG"
		;;
	*)
		echo "unsupported method: $method" >&2
		exit 64
		;;
	esac
	;;
"api repos/{owner}/{repo}/releases/tags/"*)
	# `gh api <path> --jq '.assets[] | select(.name == "X") | .id'`
	expr="$4"
	name="$(printf '%s' "$expr" | sed -n 's/.*select(\.name == "\([^"]*\)").*/\1/p')"
	[[ -n "$name" ]] || exit 64
	[[ -f "${assets}/${name}" ]] || exit 0
	printf '%s\n' "$name"
	;;
*)
	echo "unsupported gh invocation: $*" >&2
	exit 64
	;;
esac
STUB
	chmod +x "${dir}/gh"
}

setup() {
	setup_temp_dir
	WORKDIR="${BATS_TEST_TMPDIR}/work"
	mkdir -p "${WORKDIR}/dist/nuitka"
	export GH_STATE="${BATS_TEST_TMPDIR}/gh-state"
	mkdir -p "${GH_STATE}/assets"
	export GH_STUB_LOG="${BATS_TEST_TMPDIR}/gh-calls.log"
	: >"$GH_STUB_LOG"
	STUB_BIN="${BATS_TEST_TMPDIR}/stub-bin"
	write_gh_stub "$STUB_BIN"
	PATH="${STUB_BIN}:${PATH}"
	export PATH
	export GITHUB_REPOSITORY="lgtm-hq/py-lintro"
	ASSET_NAME="lintro-linux-x64"
	LOCAL_FILE="${WORKDIR}/dist/nuitka/${ASSET_NAME}"
	create_fake_binary "$LOCAL_FILE" "fresh-build"
}

teardown() {
	teardown_temp_dir
}

@test "upload_release_asset.sh: --help exits 0 and explains the swap" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "upload-then-swap"
	assert_output --partial "<asset-name>.new"
}

@test "upload_release_asset.sh: missing args exits 2" {
	run "$SCRIPT" v1.2.3
	assert_failure
	assert_equal "2" "$status"
}

@test "upload_release_asset.sh: a missing local file exits 1" {
	run "$SCRIPT" v1.2.3 "${WORKDIR}/nope"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "File not found"
}

@test "upload_release_asset.sh: an empty release tag exits 1" {
	run "$SCRIPT" "" "$LOCAL_FILE"
	assert_failure
	assert_output --partial "Release tag is required"
}

@test "upload_release_asset.sh: publishes a first-time asset under its final name" {
	run "$SCRIPT" v1.2.3 "$LOCAL_FILE"
	assert_success
	assert_output --partial "Published ${ASSET_NAME}"
	assert_equal "fresh-build" "$(cat "${GH_STATE}/assets/${ASSET_NAME}")"
	# No staging asset is left behind.
	[[ ! -e "${GH_STATE}/assets/${ASSET_NAME}.new" ]]
	# Nothing was deleted: there was nothing to replace.
	run grep -c "^delete" "$GH_STUB_LOG"
	assert_output "0"
}

@test "upload_release_asset.sh: replaces an existing asset only after the new one is up" {
	printf 'old-build\n' >"${GH_STATE}/assets/${ASSET_NAME}"

	run "$SCRIPT" v1.2.3 "$LOCAL_FILE"
	assert_success
	assert_equal "fresh-build" "$(cat "${GH_STATE}/assets/${ASSET_NAME}")"
	[[ ! -e "${GH_STATE}/assets/${ASSET_NAME}.new" ]]
	# Ordering is the whole point: the replacement is uploaded and verified
	# before the published asset is deleted, so the delete is the last-but-one
	# call and the rename immediately follows it.
	assert_equal "delete ${ASSET_NAME}" "$(sed -n 1p "$GH_STUB_LOG")"
	assert_equal "rename ${ASSET_NAME}.new -> ${ASSET_NAME}" "$(sed -n 2p "$GH_STUB_LOG")"
}

@test "upload_release_asset.sh: promotes a matching staging asset from a killed swap" {
	# A kill between the delete and the rename leaves only <asset>.new. The
	# retry must finish that swap, not delete the release's only copy and
	# re-upload it.
	printf 'fresh-build\n' >"${GH_STATE}/assets/${ASSET_NAME}.new"

	run "$SCRIPT" v1.2.3 "$LOCAL_FILE"
	assert_success
	assert_output --partial "Promoting the ${ASSET_NAME}.new"
	assert_equal "fresh-build" "$(cat "${GH_STATE}/assets/${ASSET_NAME}")"
	[[ ! -e "${GH_STATE}/assets/${ASSET_NAME}.new" ]]
	# Renamed in place: nothing was deleted and nothing was re-uploaded.
	assert_equal "rename ${ASSET_NAME}.new -> ${ASSET_NAME}" "$(sed -n 1p "$GH_STUB_LOG")"
	run grep -c "^delete" "$GH_STUB_LOG"
	assert_output "0"
}

@test "upload_release_asset.sh: clears a stale staging asset from a killed attempt" {
	printf 'old-build\n' >"${GH_STATE}/assets/${ASSET_NAME}"
	printf 'half-uploaded\n' >"${GH_STATE}/assets/${ASSET_NAME}.new"

	run "$SCRIPT" v1.2.3 "$LOCAL_FILE"
	assert_success
	assert_output --partial "Removing stale ${ASSET_NAME}.new"
	assert_equal "fresh-build" "$(cat "${GH_STATE}/assets/${ASSET_NAME}")"
	[[ ! -e "${GH_STATE}/assets/${ASSET_NAME}.new" ]]
}

@test "upload_release_asset.sh: replaces a stale staging asset when no live asset remains" {
	# The rebuild path after reuse_release_asset.sh declined a stale
	# <asset>.new: the leftover is from some other build, so it is dropped and
	# the freshly built binary is published under the final name.
	printf 'some-other-build\n' >"${GH_STATE}/assets/${ASSET_NAME}.new"

	run "$SCRIPT" v1.2.3 "$LOCAL_FILE"
	assert_success
	assert_output --partial "Removing stale ${ASSET_NAME}.new"
	assert_equal "fresh-build" "$(cat "${GH_STATE}/assets/${ASSET_NAME}")"
	[[ ! -e "${GH_STATE}/assets/${ASSET_NAME}.new" ]]
}

@test "upload_release_asset.sh: a corrupted upload fails without touching the live asset" {
	printf 'old-build\n' >"${GH_STATE}/assets/${ASSET_NAME}"
	export GH_STUB_CORRUPT_UPLOAD=1

	run "$SCRIPT" v1.2.3 "$LOCAL_FILE"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "does not match local"
	# The published binary is untouched and the bad staging asset is gone.
	assert_equal "old-build" "$(cat "${GH_STATE}/assets/${ASSET_NAME}")"
	[[ ! -e "${GH_STATE}/assets/${ASSET_NAME}.new" ]]
}

@test "upload_release_asset.sh: honours an explicit asset name" {
	create_fake_binary "${WORKDIR}/lintro" "fresh-build"
	run "$SCRIPT" v1.2.3 "${WORKDIR}/lintro" lintro-macos-arm64
	assert_success
	assert_equal "fresh-build" "$(cat "${GH_STATE}/assets/lintro-macos-arm64")"
}

@test "upload_release_asset.sh: fails when the gh CLI is unavailable" {
	stub_path="$(make_stub_path "${BATS_TEST_TMPDIR}/nogh" bash dirname cat basename)"
	run env PATH="$stub_path" "$SCRIPT" v1.2.3 "$LOCAL_FILE"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "gh CLI is required"
}

@test "upload_release_asset.sh: matches the Linux workflow argv contract" {
	cd "$WORKDIR"
	export RELEASE_TAG=v1.2.3
	export BUILD_ARCH=x64

	run bash -c '"$0" "$RELEASE_TAG" "dist/nuitka/lintro-linux-$BUILD_ARCH"' "$SCRIPT"
	assert_success
	assert_equal "fresh-build" "$(cat "${GH_STATE}/assets/lintro-linux-x64")"
}
