#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/build/finalize_binary.sh

load "../../helpers/common"

SCRIPT="${BUILD_SCRIPTS_DIR}/finalize_binary.sh"

setup() {
	setup_temp_dir
	setup_github_env
	WORKDIR="${BATS_TEST_TMPDIR}/work"
	mkdir -p "${WORKDIR}/dist/nuitka"
	SOURCE="${WORKDIR}/dist/nuitka/lintro"
	TARGET="${WORKDIR}/dist/nuitka/lintro-macos-arm64"
	create_fake_binary "$SOURCE" "lintro-arm64-build"
}

teardown() {
	teardown_temp_dir
}

@test "finalize_binary.sh: --help exits 0" {
	run bash "$SCRIPT" --help
	assert_success
	assert_output --partial "Finalize a built lintro binary"
}

@test "finalize_binary.sh: missing args exits 2" {
	run bash "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
}

@test "finalize_binary.sh: renames binary and writes sha256 output" {
	run bash "$SCRIPT" "$SOURCE" "$TARGET" arm64
	assert_success
	[[ -f "$TARGET" ]]
	[[ ! -f "$SOURCE" ]]
	assert_output --partial "SHA256 for arm64:"
	assert_output --partial "Finalized ${TARGET}:"
	expected_sha="$(compute_expected_sha256 "$TARGET")"
	assert_equal "$expected_sha" "$(get_github_output sha256)"
}

@test "finalize_binary.sh: fails when source binary is missing" {
	run bash "$SCRIPT" "${WORKDIR}/missing" "$TARGET"
	assert_failure
	assert_output --partial "Source binary not found"
}
