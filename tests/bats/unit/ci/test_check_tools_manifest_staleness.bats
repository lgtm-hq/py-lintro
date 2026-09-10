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
	mkdir -p "${SEED}/lintro/tools" "${SEED}/docker" \
		"${SEED}/lintro_build/versions" "${SEED}/scripts/ci"
	cd "${SEED}" || return 1
	git init --quiet --initial-branch=main .
	git config user.email 'test@example.com'
	git config user.name 'Test'
	printf 'RUFF = "0.1.0"\n' >lintro/_tool_versions.py
	printf '{}\n' >lintro/tools/manifest.src.json
	printf 'FROM debian\n' >docker/tools.Dockerfile
	printf '{"devDependencies": {"prettier": "3.0.0"}}\n' >package.json
	printf '[project]\nname = "lintro"\n' >pyproject.toml
	printf 'semgrep==1.0.0\n' >requirements-semgrep.txt
	printf 'SEED = {}\n' >lintro/_tool_packages.py
	printf 'MANIFEST = "lintro/tools/manifest.json"\n' >lintro_build/versions/paths.py
	printf 'print("generate")\n' >scripts/ci/generate-tool-versions.py
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
		CANDIDATE_FETCH_DELAY_SECONDS=0 \
		GITHUB_STEP_SUMMARY="${GITHUB_STEP_SUMMARY:-${BATS_TEST_TMPDIR}/summary}" \
		"$@" \
		"$SCRIPT"
}

@test "check-tools-manifest-staleness.sh: --help exits 0" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "Refuse promotion of a tools-image candidate"
	# Every environment knob the guard reads is documented.
	for name in CANDIDATE_SHA CANDIDATE_PR MAIN_SHA FORCE_PUBLISH \
		MANIFEST_PATHS GIT_REMOTE CANDIDATE_FETCH_REF GITHUB_STEP_SUMMARY; do
		assert_output --partial "$name"
	done
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

@test "refuses when main has a newer npm pin the rendered manifest reads" {
	# package.json is not a seed file, but manifest.json's npm versions are
	# generated from it, so a candidate built before this lands is stale.
	printf '{"devDependencies": {"prettier": "3.1.0"}}\n' >package.json
	git add -A
	git commit --quiet -m 'chore(deps): bump prettier'
	newer="$(git rev-parse HEAD)"

	run run_guard env
	assert_failure
	assert_output --partial "refusing to promote: main has newer tool manifest commits"
	assert_output --partial "$newer"
	assert_output --partial "package.json"
}

@test "refuses when main has a newer semgrep pin" {
	printf 'semgrep==1.1.0\n' >requirements-semgrep.txt
	git add -A
	git commit --quiet -m 'chore(deps): bump semgrep'

	run run_guard env
	assert_failure
	assert_output --partial "refusing to promote"
	assert_output --partial "requirements-semgrep.txt"
}

@test "refuses when only the generator code changed on main" {
	# The rendered manifest is produced by this code during the image build
	# and by the gates on main, so a generator change alone can strand a
	# candidate.
	printf 'MANIFEST = "lintro/tools/manifest.json"  # reordered\n' \
		>lintro_build/versions/paths.py
	git add -A
	git commit --quiet -m 'refactor(build): tidy generator paths'
	newer="$(git rev-parse HEAD)"

	run run_guard env
	assert_failure
	assert_output --partial "refusing to promote: main has newer tool manifest commits"
	assert_output --partial "$newer"
	assert_output --partial "lintro_build/versions/paths.py"
}

@test "refuses when only the generator entry point changed on main" {
	printf 'print("generate")  # tweak\n' >scripts/ci/generate-tool-versions.py
	git add -A
	git commit --quiet -m 'chore(ci): tweak the generator shim'

	run run_guard env
	assert_failure
	assert_output --partial "scripts/ci/generate-tool-versions.py"
}

@test "a failing fetch reports git's own stderr in the refusal" {
	run run_guard env GIT_REMOTE=no-such-remote
	assert_failure
	assert_output --partial "git fetch:"
	assert_output --partial "no-such-remote"
	assert_output --partial "is not available in this checkout"
}

@test "the candidate fetch is retried once before giving up" {
	run run_guard env GIT_REMOTE=no-such-remote
	assert_failure
	assert_output --partial "candidate fetch failed (attempt 1/2)"
}

@test "rejects a non-numeric fetch attempt count" {
	run run_guard env CANDIDATE_FETCH_ATTEMPTS=many
	assert_failure
	assert_equal "2" "$status"
	assert_output --partial "CANDIDATE_FETCH_ATTEMPTS must be a positive integer"
}

@test "refuses from a subdirectory of the checkout" {
	# Bare pathspecs would resolve against the cwd, so a run from a
	# subdirectory must still compare the repository-relative inputs.
	printf 'RUFF = "0.3.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'chore(deps): bump ruff again (#2)'
	main_sha="$(git rev-parse main)"
	mkdir -p sub/dir
	cd sub/dir || return 1

	run env CANDIDATE_SHA="${CANDIDATE_ABBREV}" \
		CANDIDATE_PR="${CANDIDATE_PR_NUMBER}" \
		MAIN_SHA="$main_sha" \
		CANDIDATE_FETCH_DELAY_SECONDS=0 \
		GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary" "$SCRIPT"
	assert_failure
	assert_output --partial "refusing to promote: main has newer tool manifest commits"
	assert_output --partial "lintro/_tool_versions.py"
}

@test "outside a git repository the guard is a usage error" {
	cd "${BATS_TEST_TMPDIR}" || return 1
	mkdir -p not-a-repo
	cd not-a-repo || return 1

	run env CANDIDATE_SHA="${CANDIDATE_ABBREV}" MAIN_SHA="${CANDIDATE_FULL}" \
		"$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
	assert_output --partial "not inside a git repository"
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

@test "an empty MANIFEST_PATHS is a usage error" {
	run run_guard env MANIFEST_PATHS=" "
	assert_failure
	assert_equal "2" "$status"
	assert_output --partial "MANIFEST_PATHS did not contain any paths"
}

@test "a full 40-hex candidate SHA is fetched without the PR head" {
	# GitHub serves a reachable commit by its full object id; a stock upload-pack
	# refuses that, so the fixture upstream opts in the way GitHub does.
	git -C "$UPSTREAM" config uploadpack.allowAnySHA1InWant true

	run env CANDIDATE_SHA="${CANDIDATE_FULL}" \
		MAIN_SHA="$(git rev-parse main)" \
		GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary" "$SCRIPT"
	assert_success
	assert_output --partial "match candidate commit ${CANDIDATE_FULL}"
	# The PR-head fetch destination must stay untouched on this path.
	run git rev-parse --verify --quiet refs/lintro/tools-candidate
	assert_failure
}

@test "GIT_REMOTE selects the remote the candidate is fetched from" {
	git remote rename origin upstream

	# Without the override the default remote does not exist any more.
	run run_guard env
	assert_failure
	assert_output --partial "is not available in this checkout"

	run run_guard env GIT_REMOTE=upstream
	assert_success
	assert_output --partial "match candidate commit ${CANDIDATE_FULL}"
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
