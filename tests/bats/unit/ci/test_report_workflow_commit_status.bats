#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/maintenance/report-workflow-commit-status.sh (#2603)

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/maintenance/report-workflow-commit-status.sh"

setup() {
	setup_temp_dir

	# Stubbed gh: records the status request so each test can assert the
	# state and context the script chose, without a token or a network.
	GH_LOG="${BATS_TEST_TMPDIR}/gh.log"
	export GH_LOG
	cat >"${BATS_TEST_TMPDIR}/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >>"${GH_LOG}"
STUB
	chmod +x "${BATS_TEST_TMPDIR}/gh"
	export PATH="${BATS_TEST_TMPDIR}:${PATH}"

	export STATUS_CONTEXT="ghcr-cleanup"
	export GITHUB_REPOSITORY="lgtm-hq/py-lintro"
	export GITHUB_SHA="1111111111111111111111111111111111111111"
	export GITHUB_SERVER_URL="https://github.com"
	export GITHUB_RUN_ID="42"
}

teardown() {
	teardown_temp_dir
}

@test "report-workflow-commit-status: --help exits 0" {
	run bash "${SCRIPT}" --help
	[ "$status" -eq 0 ]
	[[ "$output" == *"STATUS_CONTEXT"* ]]
}

@test "report-workflow-commit-status: requires STATUS_CONTEXT and JOB_RESULTS" {
	run bash -c 'unset STATUS_CONTEXT; JOB_RESULTS=success bash "$1"' _ "${SCRIPT}"
	[ "$status" -ne 0 ]
	[[ "$output" == *"STATUS_CONTEXT is required"* ]]

	run bash -c 'unset JOB_RESULTS; bash "$1"' _ "${SCRIPT}"
	[ "$status" -ne 0 ]
	[[ "$output" == *"JOB_RESULTS is required"* ]]
}

@test "report-workflow-commit-status: all-success posts a success status" {
	JOB_RESULTS="success success skipped" run bash "${SCRIPT}"
	[ "$status" -eq 0 ]
	run cat "${GH_LOG}"
	[[ "$output" == *"repos/lgtm-hq/py-lintro/statuses/${GITHUB_SHA}"* ]]
	[[ "$output" == *"state=success"* ]]
	[[ "$output" == *"no job failed or was cancelled (3 summarised)"* ]]
	[[ "$output" == *"context=ghcr-cleanup"* ]]
	[[ "$output" == *"target_url=https://github.com/lgtm-hq/py-lintro/actions/runs/42"* ]]
}

@test "report-workflow-commit-status: one failed job posts a failure status" {
	JOB_RESULTS="success failure success" run bash "${SCRIPT}"
	[ "$status" -eq 0 ]
	[[ "$output" == *"ghcr-cleanup: failure"* ]]
	run cat "${GH_LOG}"
	[[ "$output" == *"state=failure"* ]]
	[[ "$output" == *"1 of 3 job(s) failed or were cancelled"* ]]
}

@test "report-workflow-commit-status: a cancelled job counts as a failure" {
	# The prune's own failure cancels its sibling job, so cancelled must not
	# read as green.
	JOB_RESULTS="cancelled success" run bash "${SCRIPT}"
	[ "$status" -eq 0 ]
	run cat "${GH_LOG}"
	[[ "$output" == *"state=failure"* ]]
}

@test "report-workflow-commit-status: an unknown result is refused" {
	JOB_RESULTS="success bogus" run bash "${SCRIPT}"
	[ "$status" -ne 0 ]
	[[ "$output" == *"unknown job result 'bogus'"* ]]
	[ ! -s "${GH_LOG}" ]
}
