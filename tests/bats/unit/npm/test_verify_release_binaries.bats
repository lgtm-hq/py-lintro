#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: scripts/ci/npm/verify_release_binaries.sh proves each downloaded
# release binary against the release's SHA256SUMS and its build-binaries.yml
# attestation before stage_binaries.py copies it into the npm tree (#2632).
# A tampered binary, an unlisted one, or a failed attestation must fail
# before anything is staged.

load "../../helpers/common"

SCRIPT="${NPM_SCRIPTS_DIR}/verify_release_binaries.sh"

setup() {
	setup_temp_dir
	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	CALL_LOG="${BATS_TEST_TMPDIR}/gh-calls.log"
	make_stub_path "$STUB_BIN" bash awk tail cat >/dev/null
	# The digest tool is whichever the host has; the script accepts both.
	if command -v sha256sum >/dev/null 2>&1; then
		make_stub_path "$STUB_BIN" sha256sum >/dev/null
	else
		make_stub_path "$STUB_BIN" shasum >/dev/null
	fi
	export CALL_LOG
	BIN_DIR="${BATS_TEST_TMPDIR}/binaries"
	export BINARIES_DIR="$BIN_DIR"
	export ATTESTATION_REPO="lgtm-hq/py-lintro"
	export BINARY_SIGNER_WORKFLOW="lgtm-hq/py-lintro/.github/workflows/build-binaries.yml"
	export GH_TOKEN="stub"
	_lay_out_release
}

teardown() {
	teardown_temp_dir
}

# Three fake binaries in the download layout plus a manifest over them, the
# way download_release_binaries.sh leaves them.
_lay_out_release() {
	local name
	: >"${BIN_DIR}.manifest"
	for name in lintro-macos-arm64 lintro-linux-arm64 lintro-linux-x64; do
		create_fake_binary "${BIN_DIR}/${name}/${name}" "binary ${name}"
		printf '%s  %s\n' "$(compute_expected_sha256 "${BIN_DIR}/${name}/${name}")" "$name" >>"${BIN_DIR}.manifest"
	done
	# Unrelated assets sit in the real manifest too; they must be ignored.
	printf '%s  %s\n' "0000000000000000000000000000000000000000000000000000000000000000" "lintro.1" >>"${BIN_DIR}.manifest"
	mv "${BIN_DIR}.manifest" "${BIN_DIR}/SHA256SUMS"
}

# A fake `gh attestation verify` that records its argv; FAIL_FOR names a
# binary whose verification must fail.
_install_fake_gh() {
	cat >"${STUB_BIN}/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALL_LOG"
[[ "$1" == "attestation" && "$2" == "verify" ]] || {
	echo "unexpected gh invocation: $*" >&2
	exit 99
}
if [[ -n "${FAIL_FOR:-}" && "$3" == *"/${FAIL_FOR}/${FAIL_FOR}" ]]; then
	echo "Error: no attestations found for $3" >&2
	exit 1
fi
EOF
	chmod +x "${STUB_BIN}/gh"
}

@test "verify_release_binaries.sh: --help exits 0" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "BINARY_SIGNER_WORKFLOW"
}

@test "verify_release_binaries.sh: refuses to run without the signer inputs" {
	_install_fake_gh
	PATH="$STUB_BIN" BINARY_SIGNER_WORKFLOW="" run "$SCRIPT"
	[[ "$status" -eq 2 ]]
	assert_output --partial "BINARY_SIGNER_WORKFLOW is required"
}

@test "verify_release_binaries.sh: passes when every digest and attestation checks out" {
	_install_fake_gh
	PATH="$STUB_BIN" run "$SCRIPT"
	assert_success
	assert_output --partial "Verified 3 release binaries"
	for name in lintro-macos-arm64 lintro-linux-arm64 lintro-linux-x64; do
		grep -q -- "attestation verify ${BIN_DIR}/${name}/${name} --repo lgtm-hq/py-lintro --signer-workflow lgtm-hq/py-lintro/.github/workflows/build-binaries.yml" "$CALL_LOG"
	done
}

@test "verify_release_binaries.sh: a tampered binary fails before any attestation call" {
	_install_fake_gh
	printf 'replaced bytes\n' >"${BIN_DIR}/lintro-macos-arm64/lintro-macos-arm64"
	PATH="$STUB_BIN" run "$SCRIPT"
	assert_failure
	assert_output --partial "sha256 mismatch for lintro-macos-arm64"
	[[ ! -e "$CALL_LOG" ]]
}

@test "verify_release_binaries.sh: a binary the manifest does not list is refused" {
	_install_fake_gh
	grep -v "lintro-linux-x64" "${BIN_DIR}/SHA256SUMS" >"${BIN_DIR}/SHA256SUMS.new"
	mv "${BIN_DIR}/SHA256SUMS.new" "${BIN_DIR}/SHA256SUMS"
	PATH="$STUB_BIN" run "$SCRIPT"
	assert_failure
	assert_output --partial "no entry for lintro-linux-x64"
}

@test "verify_release_binaries.sh: a failed attestation fails the run" {
	_install_fake_gh
	PATH="$STUB_BIN" FAIL_FOR=lintro-linux-arm64 run "$SCRIPT"
	assert_failure
	assert_output --partial "attestation verification failed for lintro-linux-arm64"
}

@test "verify_release_binaries.sh: a missing manifest fails closed" {
	_install_fake_gh
	rm "${BIN_DIR}/SHA256SUMS"
	PATH="$STUB_BIN" run "$SCRIPT"
	assert_failure
	assert_output --partial "missing or empty checksums manifest"
}
