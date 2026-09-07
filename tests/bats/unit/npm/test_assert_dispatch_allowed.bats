#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/npm/assert_dispatch_allowed.sh (issue #2247).

load "../../helpers/common"

SCRIPT="${NPM_SCRIPTS_DIR}/assert_dispatch_allowed.sh"

setup() {
	setup_temp_dir
}

teardown() {
	teardown_temp_dir
}

@test "assert_dispatch_allowed.sh: --help exits 0" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "Refuse live workflow_dispatch runs"
}

@test "assert_dispatch_allowed.sh: allows the tag pipeline workflow_call path" {
	EVENT_NAME=push DRY_RUN=false run "$SCRIPT"
	assert_success
	assert_output --partial "proceeding"
}

@test "assert_dispatch_allowed.sh: allows a dry-run dispatch" {
	EVENT_NAME=workflow_dispatch DRY_RUN=true run "$SCRIPT"
	assert_success
	assert_output --partial "Dry-run dispatch"
}

@test "assert_dispatch_allowed.sh: refuses a live dispatch" {
	EVENT_NAME=workflow_dispatch DRY_RUN=false run "$SCRIPT"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "cannot be started from a direct dispatch"
	assert_output --partial "publish-pypi-on-tag.yml"
}

@test "assert_dispatch_allowed.sh: refuses a dispatch with dry_run unset" {
	EVENT_NAME=workflow_dispatch run "$SCRIPT"
	assert_failure
	assert_output --partial "cannot be started from a direct dispatch"
}

@test "assert_dispatch_allowed.sh: refuses a dispatch with a non-boolean dry_run" {
	# Anything but the literal "true" is treated as a live publish: a mangled
	# expression must not open the door that a false one closes.
	EVENT_NAME=workflow_dispatch DRY_RUN="" run "$SCRIPT"
	assert_failure
	assert_output --partial "cannot be started from a direct dispatch"
}

@test "assert_dispatch_allowed.sh: allows a schedule-free empty event name" {
	# workflow_call runs report the caller's event (push / workflow_dispatch on
	# the tag pipeline); an unset EVENT_NAME must not be mistaken for a
	# dispatch of this workflow.
	DRY_RUN=false run "$SCRIPT"
	assert_success
}
