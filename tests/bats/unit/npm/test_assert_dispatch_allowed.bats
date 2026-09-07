#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/npm/assert_dispatch_allowed.sh (issue #2247).
# The guard allowlists the single entry workflow npm trusts; every other live
# entry path must be refused.

load "../../helpers/common"

SCRIPT="${NPM_SCRIPTS_DIR}/assert_dispatch_allowed.sh"

WORKFLOWS="lgtm-hq/py-lintro/.github/workflows"
TAG_PIPELINE_REF="${WORKFLOWS}/publish-pypi-on-tag.yml@refs/tags/v1.2.3"
DISPATCH_REF="${WORKFLOWS}/publish-npm.yml@refs/heads/main"

setup() {
	setup_temp_dir
	# The script falls back to the runner-provided value; a real Actions
	# environment would leak it into the no-context cases below.
	export GITHUB_WORKFLOW_REF=""
}

teardown() {
	teardown_temp_dir
}

@test "assert_dispatch_allowed.sh: --help exits 0" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "trusted tag-pipeline entry path"
}

@test "assert_dispatch_allowed.sh: allows the tag pipeline entry workflow" {
	WORKFLOW_REF="$TAG_PIPELINE_REF" DRY_RUN=false run "$SCRIPT"
	assert_success
	assert_output --partial "trusted publisher identity"
}

@test "assert_dispatch_allowed.sh: allows the tag pipeline on any ref" {
	# npm matches the workflow file, not the ref it ran on.
	WORKFLOW_REF="${WORKFLOWS}/publish-pypi-on-tag.yml@refs/heads/main" DRY_RUN=false \
		run "$SCRIPT"
	assert_success
}

@test "assert_dispatch_allowed.sh: allows a dry-run dispatch" {
	WORKFLOW_REF="$DISPATCH_REF" DRY_RUN=true run "$SCRIPT"
	assert_success
	assert_output --partial "Dry run"
}

@test "assert_dispatch_allowed.sh: refuses a live dispatch of this workflow" {
	WORKFLOW_REF="$DISPATCH_REF" DRY_RUN=false run "$SCRIPT"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "can only run from the tag pipeline"
	assert_output --partial "publish-pypi-on-tag.yml"
	# The diagnostic names the identity the run would have presented.
	assert_output --partial "$DISPATCH_REF"
}

@test "assert_dispatch_allowed.sh: refuses a live dispatch of this workflow on a tag" {
	# Dispatching publish-npm.yml against a tag ref does not help: npm matches
	# the workflow file, not the ref.
	WORKFLOW_REF="${WORKFLOWS}/publish-npm.yml@refs/tags/v1.2.3" DRY_RUN=false \
		run "$SCRIPT"
	assert_failure
}

@test "assert_dispatch_allowed.sh: refuses an unknown caller (allowlist, not denylist)" {
	# A new or renamed caller is not the trusted identity, so it must be
	# refused rather than waved through because it is not publish-npm.yml.
	WORKFLOW_REF="${WORKFLOWS}/some-new-release-pipeline.yml@refs/tags/v1.2.3" \
		DRY_RUN=false run "$SCRIPT"
	assert_failure
	assert_output --partial "can only run from the tag pipeline"
}

@test "assert_dispatch_allowed.sh: refuses a run with dry_run unset" {
	WORKFLOW_REF="$DISPATCH_REF" run "$SCRIPT"
	assert_failure
}

@test "assert_dispatch_allowed.sh: refuses a run with a non-boolean dry_run" {
	# Anything but the literal "true" is treated as a live publish: a mangled
	# expression must not open the door that a false one closes.
	WORKFLOW_REF="$DISPATCH_REF" DRY_RUN="" run "$SCRIPT"
	assert_failure
}

@test "assert_dispatch_allowed.sh: a live run with no entry workflow is refused" {
	# Nothing proves the run can authenticate: fail closed.
	DRY_RUN=false run "$SCRIPT"
	assert_failure
	assert_output --partial "no entry workflow"
}

@test "assert_dispatch_allowed.sh: a dry run is allowed with no context at all" {
	DRY_RUN=true run "$SCRIPT"
	assert_success
}

@test "assert_dispatch_allowed.sh: falls back to the runner environment" {
	# A dropped `env:` mapping in the workflow must still gate the publish.
	GITHUB_WORKFLOW_REF="$DISPATCH_REF" DRY_RUN=false run "$SCRIPT"
	assert_failure

	GITHUB_WORKFLOW_REF="$TAG_PIPELINE_REF" DRY_RUN=false run "$SCRIPT"
	assert_success
}
