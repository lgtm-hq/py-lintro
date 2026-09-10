#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/check-tools-manifest-staleness.sh (#2497)

load "../../helpers/common"

CI_SCRIPTS_DIR="${PROJECT_ROOT}/scripts/ci"

setup() {
	setup_temp_dir
	setup_github_env
	SCRIPT="${CI_SCRIPTS_DIR}/check-tools-manifest-staleness.sh"
	CANDIDATE_PR_NUMBER=7

	# An "upstream" holding main plus the candidate branch parked under
	# refs/pull/<n>/head, exactly like GitHub keeps a merged Renovate PR head
	# after the branch is deleted.
	UPSTREAM="${BATS_TEST_TMPDIR}/upstream.git"
	git init --quiet --bare "$UPSTREAM"

	SEED="${BATS_TEST_TMPDIR}/seed"
	mkdir -p "${SEED}/lintro/tools" "${SEED}/docker"
	cd "${SEED}" || return 1
	git init --quiet --initial-branch=main .
	git config user.email 'test@example.com'
	git config user.name 'Test'
	printf 'RUFF = "0.1.0"\n' >lintro/_tool_versions.py
	printf '{}\n' >lintro/tools/manifest.src.json
	printf 'FROM debian\n' >docker/tools.Dockerfile
	printf 'unrelated\n' >README.md
	git add -A
	git commit --quiet -m 'base'
	BASE_SHA="$(git rev-parse HEAD)"

	# The candidate branch bumps a tool version; the image is built from its
	# head, and the candidate tag carries only the first 12 characters of it.
	git checkout --quiet -b candidate
	printf 'RUFF = "0.2.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'bump ruff'
	CANDIDATE_FULL="$(git rev-parse HEAD)"
	CANDIDATE_ABBREV="${CANDIDATE_FULL:0:12}"

	# main receives the same content (a squash merge of the candidate branch).
	git checkout --quiet main
	printf 'RUFF = "0.2.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'chore(deps): bump ruff (#1)'
	git push --quiet "$UPSTREAM" main
	git push --quiet "$UPSTREAM" "candidate:refs/pull/${CANDIDATE_PR_NUMBER}/head"

	# The promote job's checkout: main only. The candidate commit is absent
	# until the guard fetches the PR head.
	REPO="${BATS_TEST_TMPDIR}/repo"
	# --no-local: a local clone hardlinks the whole object store, which would
	# hand the checkout the candidate commit the guard is meant to fetch.
	git clone --quiet --no-local "$UPSTREAM" "$REPO"
	cd "${REPO}" || return 1
	git config user.email 'test@example.com'
	git config user.name 'Test'
	run git cat-file -e "${CANDIDATE_FULL}^{commit}"
	assert_failure
}

run_guard() {
	env CANDIDATE_SHA="${CANDIDATE_ABBREV}" \
		CANDIDATE_PR="${CANDIDATE_PR_NUMBER}" \
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

@test "an abbreviated candidate SHA resolves through the PR head fetch" {
	run run_guard env
	assert_success
	assert_output --partial "match candidate commit ${CANDIDATE_FULL}"
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

@test "candidate-side divergence is reported as such, not as a main commit" {
	# main never carried the candidate's manifest change: the content differs
	# but no manifest-touching commit lies between the two.
	git reset --hard --quiet "$BASE_SHA"

	run run_guard env
	assert_failure
	assert_output --partial "tool manifest inputs differ between candidate commit ${CANDIDATE_FULL}"
	assert_output --partial "and main ${BASE_SHA}"
	assert_output --partial "candidate-side divergence"
	assert_output --partial "rebuild from main with force_publish=true"
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
	run env CANDIDATE_SHA="${CANDIDATE_ABBREV}" MAIN_SHA="" "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
	assert_output --partial "MAIN_SHA is required"
}

@test "fails closed when the PR head does not carry the candidate commit" {
	run run_guard env CANDIDATE_SHA=0000000000000000000000000000000000000000
	assert_failure
	assert_output --partial "refusing to promote"
	assert_output --partial "is not available in this checkout"
	assert_output --partial "refs/pull/${CANDIDATE_PR_NUMBER}/head"
}

@test "fails closed when an abbreviated SHA has no PR number to fetch" {
	run env CANDIDATE_SHA="${CANDIDATE_ABBREV}" \
		MAIN_SHA="$(git rev-parse main)" "$SCRIPT"
	assert_failure
	assert_output --partial "refusing to promote: candidate commit ${CANDIDATE_ABBREV} is not available"
}

@test "fails closed when the abbreviation is ambiguous" {
	# Real 12-character collisions are not constructible in a test, so git's
	# ambiguity report is stubbed: what matters is that the guard classifies
	# it as ambiguous instead of reporting a plain missing commit.
	stub_bin="${BATS_TEST_TMPDIR}/stub-bin"
	mkdir -p "$stub_bin"
	real_git="$(command -v git)"
	cat >"${stub_bin}/git" <<STUB
#!/usr/bin/env bash
if [[ "\$1" == "rev-parse" && "\$2" == "--verify" && "\$3" == "${CANDIDATE_ABBREV}"* ]]; then
	echo "error: short object ID ${CANDIDATE_ABBREV} is ambiguous" >&2
	exit 128
fi
exec "${real_git}" "\$@"
STUB
	chmod +x "${stub_bin}/git"

	run env PATH="${stub_bin}:${PATH}" \
		CANDIDATE_SHA="${CANDIDATE_ABBREV}" \
		CANDIDATE_PR="${CANDIDATE_PR_NUMBER}" \
		MAIN_SHA="$(git rev-parse main)" "$SCRIPT"
	assert_failure
	assert_output --partial "is an ambiguous abbreviation in this checkout"
	assert_output --partial "rebuild from main with force_publish=true"
}
