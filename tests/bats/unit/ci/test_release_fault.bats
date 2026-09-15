#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/release-fault.sh, the #2633 fault injector

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/release-fault.sh"

setup() {
	setup_temp_dir
	export GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary"
}

@test "help flag prints usage" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "RELEASE_FAULT"
	assert_output --partial "fail-build"
}

@test "unset RELEASE_FAULT is a no-op" {
	run env -u RELEASE_FAULT "$SCRIPT" fail-build
	assert_success
	assert_output --partial "nothing injected"
	[[ ! -s "$GITHUB_STEP_SUMMARY" ]]
}

@test "empty RELEASE_FAULT is a no-op" {
	run env RELEASE_FAULT="" "$SCRIPT" fail-publish-npm
	assert_success
	assert_output --partial "nothing injected"
}

@test "a different fault name is a no-op" {
	run env RELEASE_FAULT=fail-publish-npm "$SCRIPT" fail-build
	assert_success
	assert_output --partial "does not name this step"
	[[ ! -s "$GITHUB_STEP_SUMMARY" ]]
}

@test "matching fault name fails with a named error annotation" {
	run env RELEASE_FAULT=fail-build "$SCRIPT" fail-build
	assert_failure
	[[ "$status" -eq 1 ]]
	assert_output --partial "::error"
	assert_output --partial "RELEASE_FAULT=fail-build"
	grep -q 'fail-build' "$GITHUB_STEP_SUMMARY"
}

@test "fail-publish-npm fires only for its own name" {
	run env RELEASE_FAULT=fail-publish-npm "$SCRIPT" fail-publish-npm
	assert_failure
	[[ "$status" -eq 1 ]]
	assert_output --partial "RELEASE_FAULT=fail-publish-npm"
}

@test "surrounding whitespace in RELEASE_FAULT does not match" {
	# classify-tag trims the variable before it reaches the step; the script
	# itself compares exactly so an untrimmed value cannot fire by accident.
	run env RELEASE_FAULT=" fail-build " "$SCRIPT" fail-build
	assert_success
}

@test "unknown fault name is a usage error" {
	run "$SCRIPT" fail-everything
	[[ "$status" -eq 2 ]]
	assert_output --partial "unknown fault name"
}

@test "missing fault name is a usage error" {
	run "$SCRIPT"
	[[ "$status" -eq 2 ]]
}
