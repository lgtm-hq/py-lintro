#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/build/create_universal.sh

load "../../helpers/common"

SCRIPT="${BUILD_SCRIPTS_DIR}/create_universal.sh"

setup() {
	setup_temp_dir
	WORKDIR="${BATS_TEST_TMPDIR}/work"
	mkdir -p "${WORKDIR}/binaries"
	ARM64="${WORKDIR}/binaries/lintro-macos-arm64"
	X86="${WORKDIR}/binaries/lintro-macos-x86_64"
	OUTPUT="${WORKDIR}/binaries/lintro-macos-universal"

	if command -v cc >/dev/null 2>&1; then
		cat >"${BATS_TEST_TMPDIR}/stub.c" <<'EOF'
int main(void) {
	return 0;
}
EOF
		cc -arch arm64 -o "$ARM64" "${BATS_TEST_TMPDIR}/stub.c" 2>/dev/null || true
		cc -arch x86_64 -o "$X86" "${BATS_TEST_TMPDIR}/stub.c" 2>/dev/null || true
	fi
}

teardown() {
	teardown_temp_dir
}

@test "create_universal.sh: --help exits 0" {
	run bash "$SCRIPT" --help
	assert_success
	assert_output --partial "Create a macOS universal lintro binary"
}

@test "create_universal.sh: missing args exits 2" {
	run bash "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
}

@test "create_universal.sh: fails when input binary is missing" {
	create_fake_binary "$X86" "lintro-x86_64"
	run bash "$SCRIPT" "${WORKDIR}/missing-arm64" "$X86" "$OUTPUT"
	assert_failure
	assert_output --partial "Input binary not found"
}

@test "create_universal.sh: creates universal binary with lipo when available" {
	if ! command -v lipo >/dev/null 2>&1; then
		skip "lipo not available on this host"
	fi
	if [[ ! -f "$ARM64" || ! -f "$X86" ]]; then
		skip "could not compile arm64 and x86_64 stub binaries"
	fi

	run bash "$SCRIPT" "$ARM64" "$X86" "$OUTPUT"
	assert_success
	[[ -f "$OUTPUT" ]]
	[[ -x "$OUTPUT" ]]
	assert_output --partial "lintro-macos-universal"
}
