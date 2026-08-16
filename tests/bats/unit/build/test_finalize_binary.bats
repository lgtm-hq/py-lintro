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
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "Finalize a built lintro binary"
}

@test "finalize_binary.sh: missing args exits 2" {
	run "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
}

@test "finalize_binary.sh: renames binary and writes sha256 output" {
	# create_fake_binary leaves the source executable and `mv` preserves that
	# bit, so without stripping it first this would pass even if the script
	# stopped running chmod +x. Nuitka output is not reliably executable.
	chmod a-x "$SOURCE"
	run "$SCRIPT" "$SOURCE" "$TARGET" arm64
	assert_success
	[[ -f "$TARGET" ]]
	[[ -x "$TARGET" ]]
	[[ ! -f "$SOURCE" ]]
	assert_output --partial "SHA256 for arm64:"
	assert_output --partial "Finalized ${TARGET}:"
	expected_sha="$(compute_expected_sha256 "$TARGET")"
	assert_equal "$expected_sha" "$(get_github_output sha256)"
}

@test "finalize_binary.sh: fails when source binary is missing" {
	run "$SCRIPT" "${WORKDIR}/missing" "$TARGET"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "Source binary not found"
}

@test "finalize_binary.sh: fails closed when no SHA256 tool is available" {
	# The sha256sum/shasum fallback chain ends in an explicit abort. Run with a
	# PATH holding only what the script needs to reach that branch, so neither
	# hashing tool resolves and the fail-closed path is actually taken.
	stub_path="$(make_stub_path "${BATS_TEST_TMPDIR}/stubbin" bash dirname mkdir mv chmod)"

	run env PATH="$stub_path" "$SCRIPT" "$SOURCE" "$TARGET" arm64
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "No SHA256 tool found"
	# Nothing may be published when the hash could not be computed.
	assert_equal "" "$(cat "$GITHUB_OUTPUT")"
}

@test "finalize_binary.sh: matches the macOS workflow BUILD_ARCH argv contract" {
	# Same argv/env shape as build-macos' "Finalize binary" step: BUILD_ARCH
	# arrives via env and the workflow expands it inside the quoted arguments.
	# Renaming the env var or mis-quoting an argument breaks this test.
	cd "$WORKDIR"
	create_fake_binary dist/nuitka/lintro "lintro-macos-build"
	export BUILD_ARCH=arm64

	run bash -c '"$0" dist/nuitka/lintro "dist/nuitka/lintro-macos-$BUILD_ARCH" "$BUILD_ARCH"' "$SCRIPT"
	assert_success
	[[ -f "dist/nuitka/lintro-macos-arm64" ]]
	[[ -x "dist/nuitka/lintro-macos-arm64" ]]
	assert_output --partial "SHA256 for arm64:"
	expected_sha="$(compute_expected_sha256 dist/nuitka/lintro-macos-arm64)"
	assert_equal "$expected_sha" "$(get_github_output sha256)"
}

@test "finalize_binary.sh: matches the Linux workflow BUILD_ARCH argv contract" {
	# build-linux uses the same shape but a linux- prefixed target and a
	# linux-<arch> label, which is what feeds sha256-linux-<arch>.txt.
	cd "$WORKDIR"
	create_fake_binary dist/nuitka/lintro "lintro-linux-build"
	export BUILD_ARCH=x64

	run bash -c '"$0" dist/nuitka/lintro "dist/nuitka/lintro-linux-$BUILD_ARCH" "linux-$BUILD_ARCH"' "$SCRIPT"
	assert_success
	[[ -f "dist/nuitka/lintro-linux-x64" ]]
	assert_output --partial "SHA256 for linux-x64:"
	expected_sha="$(compute_expected_sha256 dist/nuitka/lintro-linux-x64)"
	assert_equal "$expected_sha" "$(get_github_output sha256)"
}

@test "finalize_binary.sh: succeeds without GITHUB_OUTPUT set" {
	# Outside Actions there is no GITHUB_OUTPUT; the rename and hashing must
	# still succeed and nothing may be written to the (absent) output file.
	local output_file="$GITHUB_OUTPUT"
	run env -u GITHUB_OUTPUT "$SCRIPT" "$SOURCE" "$TARGET" arm64
	assert_success
	[[ -f "$TARGET" ]]
	assert_output --partial "SHA256 for arm64:"
	assert_equal "" "$(cat "$output_file")"
}
