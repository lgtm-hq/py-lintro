#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/npm/assert_dispatch_allowed.sh (issue #2247).

load "../../helpers/common"

SCRIPT="${NPM_SCRIPTS_DIR}/assert_dispatch_allowed.sh"

TAG_PIPELINE_REF="lgtm-hq/py-lintro/.github/workflows/publish-pypi-on-tag.yml@refs/tags/v1.2.3"
DISPATCH_REF="lgtm-hq/py-lintro/.github/workflows/publish-npm.yml@refs/heads/main"

setup() {
	setup_temp_dir
	# The script falls back to the runner-provided values; a real Actions
	# environment would leak them into the no-context cases below.
	export GITHUB_WORKFLOW_REF=""
	export GITHUB_EVENT_NAME=""
}

teardown() {
	teardown_temp_dir
}

@test "assert_dispatch_allowed.sh: --help exits 0" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "Refuse live workflow_dispatch runs"
}

@test "assert_dispatch_allowed.sh: allows the tag pipeline entry workflow" {
	WORKFLOW_REF="$TAG_PIPELINE_REF" EVENT_NAME=push DRY_RUN=false run "$SCRIPT"
	assert_success
	assert_output --partial "trusted publisher identity"
}

@test "assert_dispatch_allowed.sh: allows a dispatched tag-pipeline run" {
	# A workflow_call run reports the *caller's* event, so dispatching the tag
	# pipeline surfaces here as workflow_dispatch with dry_run false. The entry
	# workflow is still the trusted one, so it must not be refused.
	WORKFLOW_REF="$TAG_PIPELINE_REF" EVENT_NAME=workflow_dispatch DRY_RUN=false \
		run "$SCRIPT"
	assert_success
}

@test "assert_dispatch_allowed.sh: allows a dry-run dispatch" {
	WORKFLOW_REF="$DISPATCH_REF" EVENT_NAME=workflow_dispatch DRY_RUN=true run "$SCRIPT"
	assert_success
	assert_output --partial "Dry run"
}

@test "assert_dispatch_allowed.sh: refuses a live dispatch of this workflow" {
	WORKFLOW_REF="$DISPATCH_REF" EVENT_NAME=workflow_dispatch DRY_RUN=false run "$SCRIPT"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "cannot be started from a direct dispatch"
	assert_output --partial "publish-pypi-on-tag.yml"
	# The diagnostic names the identity the run would have presented.
	assert_output --partial "$DISPATCH_REF"
}

@test "assert_dispatch_allowed.sh: refuses a live run entering via this workflow on a tag" {
	# Dispatching publish-npm.yml against a tag ref does not help: npm matches
	# the workflow file, not the ref.
	WORKFLOW_REF="lgtm-hq/py-lintro/.github/workflows/publish-npm.yml@refs/tags/v1.2.3" \
		EVENT_NAME=workflow_dispatch DRY_RUN=false run "$SCRIPT"
	assert_failure
}

@test "assert_dispatch_allowed.sh: refuses a dispatch with dry_run unset" {
	WORKFLOW_REF="$DISPATCH_REF" EVENT_NAME=workflow_dispatch run "$SCRIPT"
	assert_failure
	assert_output --partial "cannot be started from a direct dispatch"
}

@test "assert_dispatch_allowed.sh: refuses a dispatch with a non-boolean dry_run" {
	# Anything but the literal "true" is treated as a live publish: a mangled
	# expression must not open the door that a false one closes.
	WORKFLOW_REF="$DISPATCH_REF" EVENT_NAME=workflow_dispatch DRY_RUN="" run "$SCRIPT"
	assert_failure
}

@test "assert_dispatch_allowed.sh: falls back to the event when no workflow ref" {
	EVENT_NAME=workflow_dispatch DRY_RUN=false run "$SCRIPT"
	assert_failure
	assert_output --partial "cannot be started from a direct dispatch"

	EVENT_NAME=push DRY_RUN=false run "$SCRIPT"
	assert_success
	assert_output --partial "npm trusted publishing applies"
}

@test "assert_dispatch_allowed.sh: a dry run is allowed with no context at all" {
	DRY_RUN=true run "$SCRIPT"
	assert_success
}

@test "assert_dispatch_allowed.sh: a live run with no context at all is refused" {
	# Neither a workflow ref nor an event names an entry path, so there is
	# nothing to prove the run can authenticate: fail closed.
	run "$SCRIPT"
	assert_failure
}

@test "assert_dispatch_allowed.sh: falls back to the runner environment" {
	# A dropped `env:` mapping in the workflow must still gate the publish.
	GITHUB_WORKFLOW_REF="$DISPATCH_REF" DRY_RUN=false run "$SCRIPT"
	assert_failure

	GITHUB_WORKFLOW_REF="$TAG_PIPELINE_REF" DRY_RUN=false run "$SCRIPT"
	assert_success
}
