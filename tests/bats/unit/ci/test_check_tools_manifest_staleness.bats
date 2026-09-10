#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/check-tools-manifest-staleness.sh (#2497)

load "../../helpers/common"

CI_SCRIPTS_DIR="${PROJECT_ROOT}/scripts/ci"

setup() {
	setup_temp_dir
	setup_github_env
	SCRIPT="${CI_SCRIPTS_DIR}/check-tools-manifest-staleness.sh"

	# A throwaway repo standing in for main plus a candidate branch. The guard
	# only reads history, so fixture commits are enough to drive every branch.
	REPO="${BATS_TEST_TMPDIR}/repo"
	mkdir -p "${REPO}/lintro/tools" "${REPO}/docker"
	cd "${REPO}" || return 1
	git init --quiet --initial-branch=main .
	git config user.email 'test@example.com'
	git config user.name 'Test'
	printf 'RUFF = "0.1.0"\n' >lintro/_tool_versions.py
	printf '{}\n' >lintro/tools/manifest.src.json
	printf 'FROM debian\n' >docker/tools.Dockerfile
	printf 'unrelated\n' >README.md
	git add -A
	git commit --quiet -m 'base'

	# The candidate branch bumps a tool version; the image is built from its
	# head, which is the commit the candidate tag embeds.
	git checkout --quiet -b candidate
	printf 'RUFF = "0.2.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'bump ruff'
	CANDIDATE_SHA_VALUE="$(git rev-parse HEAD)"
	export CANDIDATE_SHA_VALUE

	# main receives the same content (a squash merge of the candidate branch).
	git checkout --quiet main
	printf 'RUFF = "0.2.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'chore(deps): bump ruff (#1)'
}

run_guard() {
	env CANDIDATE_SHA="${CANDIDATE_SHA_VALUE}" \
		MAIN_SHA="$(git rev-parse main)" \
		GITHUB_STEP_SUMMARY="${GITHUB_STEP_SUMMARY:-${BATS_TEST_TMPDIR}/summary}" \
		"$@" \
		"$SCRIPT"
}

@test "check-tools-manifest-staleness.sh: --help exits 0" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "Refuse promotion of a tools-image candidate"
}

@test "promotes when main carries only the candidate's own change" {
	run run_guard env
	assert_success
	assert_output --partial "match candidate commit"
}

@test "promotes when main only changed an unrelated path" {
	printf 'docs edit\n' >README.md
	git add -A
	git commit --quiet -m 'docs: tweak readme'

	run run_guard env
	assert_success
}

@test "refuses when main has a newer tool manifest commit" {
	printf 'RUFF = "0.3.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'chore(deps): bump ruff again (#2)'
	newer="$(git rev-parse HEAD)"

	run run_guard env
	assert_failure
	assert_output --partial "refusing to promote: main has newer tool manifest commits"
	assert_output --partial "$newer"
	assert_output --partial "rebuild from main with force_publish=true"
	assert_output --partial "lintro/_tool_versions.py"
}

@test "refusal is written to the step summary" {
	export GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary"
	: >"$GITHUB_STEP_SUMMARY"
	printf '{"tools": []}\n' >lintro/tools/manifest.src.json
	git add -A
	git commit --quiet -m 'chore: manifest source change'

	run run_guard env
	assert_failure
	run cat "$GITHUB_STEP_SUMMARY"
	assert_output --partial "refusing to promote: main has newer tool manifest commits"
}

@test "force_publish=true skips the guard and notes it in the summary" {
	export GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary"
	: >"$GITHUB_STEP_SUMMARY"
	printf 'RUFF = "0.3.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'chore(deps): bump ruff again (#2)'

	run run_guard env FORCE_PUBLISH=true
	assert_success
	assert_output --partial "force_publish=true"
	run cat "$GITHUB_STEP_SUMMARY"
	assert_output --partial "force_publish=true"
}

@test "skips when no candidate commit is provided" {
	run env CANDIDATE_SHA="" MAIN_SHA="$(git rev-parse main)" "$SCRIPT"
	assert_success
	assert_output --partial "no CANDIDATE_SHA"
}

@test "requires MAIN_SHA when a candidate commit is given" {
	run env CANDIDATE_SHA="${CANDIDATE_SHA_VALUE}" MAIN_SHA="" "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
	assert_output --partial "MAIN_SHA is required"
}

@test "fails closed when the candidate commit cannot be resolved" {
	run run_guard env CANDIDATE_SHA=0000000000000000000000000000000000000000
	assert_failure
	assert_output --partial "refusing to promote"
	assert_output --partial "is not available in this checkout"
}
