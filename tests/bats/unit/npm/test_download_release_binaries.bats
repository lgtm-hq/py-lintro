#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: scripts/ci/npm/download_release_binaries.sh fetches the three
# platform binaries AND the release's SHA256SUMS (#2632), laying them out
# for verify_release_binaries.sh and stage_binaries.py, and fails when the
# release carries no manifest.

load "../../helpers/common"

SCRIPT="${NPM_SCRIPTS_DIR}/download_release_binaries.sh"

setup() {
	setup_temp_dir
	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	CALL_LOG="${BATS_TEST_TMPDIR}/gh-calls.log"
	make_stub_path "$STUB_BIN" bash mkdir cat >/dev/null
	export CALL_LOG
	DEST="${BATS_TEST_TMPDIR}/dest"
}

teardown() {
	teardown_temp_dir
}

# A fake `gh release download` that records its argv and writes the asset
# named by --pattern into --dir. WITHOUT_CHECKSUMS=1 mimics a release that
# predates the manifest: the SHA256SUMS download produces nothing.
_install_fake_gh() {
	cat >"${STUB_BIN}/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALL_LOG"
[[ "$1" == "release" && "$2" == "download" ]] || {
	echo "unexpected gh invocation: $*" >&2
	exit 99
}
pattern=""
dir=""
while [[ $# -gt 0 ]]; do
	case "$1" in
	--pattern) pattern="$2"; shift 2 ;;
	--dir) dir="$2"; shift 2 ;;
	*) shift ;;
	esac
done
if [[ "$pattern" == "SHA256SUMS" && "${WITHOUT_CHECKSUMS:-0}" == "1" ]]; then
	exit 0
fi
printf 'asset:%s\n' "$pattern" >"${dir}/${pattern}"
EOF
	chmod +x "${STUB_BIN}/gh"
}

@test "download_release_binaries.sh: --help exits 0 and names SHA256SUMS" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "SHA256SUMS"
}

@test "download_release_binaries.sh: refuses to run without a tag and destination" {
	run "$SCRIPT" v1.2.3
	[[ "$status" -eq 2 ]]
}

@test "download_release_binaries.sh: fetches the three binaries and SHA256SUMS" {
	_install_fake_gh
	PATH="$STUB_BIN" GH_TOKEN=x run "$SCRIPT" v1.2.3 "$DEST"
	assert_success
	for name in lintro-macos-arm64 lintro-linux-arm64 lintro-linux-x64; do
		[[ -s "${DEST}/${name}/${name}" ]]
		grep -q -- "release download v1.2.3 --pattern ${name} --dir ${DEST}/${name} --clobber" "$CALL_LOG"
	done
	[[ -s "${DEST}/SHA256SUMS" ]]
	grep -q -- "release download v1.2.3 --pattern SHA256SUMS --dir ${DEST} --clobber" "$CALL_LOG"
	assert_output --partial "Downloaded 3 platform binaries and SHA256SUMS"
}

@test "download_release_binaries.sh: a release without SHA256SUMS fails the download" {
	_install_fake_gh
	PATH="$STUB_BIN" GH_TOKEN=x WITHOUT_CHECKSUMS=1 run "$SCRIPT" v1.2.3 "$DEST"
	assert_failure
	assert_output --partial "has no SHA256SUMS asset"
	[[ ! -e "${DEST}/SHA256SUMS" ]]
}
