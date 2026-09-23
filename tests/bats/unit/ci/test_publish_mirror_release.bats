#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/mirror/publish-mirror-release.sh (#2742)
#
# The script drives git, gh and the GraphQL API against a real mirror
# checkout, so these tests pin its control flow with stubbed gh/git
# behaviour: (a) auto-merge enabled and MERGED within the bound, (b) the
# merge poll timing out fails with the PR URL, (c) the rebase-onto-main
# step is gone and the stale-branch heal runs instead, and the bump commit
# is created via createCommitOnBranch, never plain git commit.

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/mirror/publish-mirror-release.sh"

setup() {
	setup_temp_dir

	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	mkdir -p "${STUB_BIN}"

	# Stub gh: records every invocation, answers the PR state poll from
	# GH_STATES (one state per poll, last one repeats), and fakes `pr list`
	# (no open PR) and `pr create` (a fixed URL).
	GH_LOG="${BATS_TEST_TMPDIR}/gh.log"
	: >"${GH_LOG}"
	cat >"${STUB_BIN}/gh" <<STUB
#!/usr/bin/env bash
echo "\$*" >>"${GH_LOG}"
if [[ "\$1" == "pr" && "\$2" == "list" ]]; then
	echo ""
	exit 0
fi
if [[ "\$1" == "pr" && "\$2" == "create" ]]; then
	echo "https://github.com/lgtm-hq/lintro-pre-commit/pull/22"
	exit 0
fi
if [[ "\$1" == "pr" && "\$2" == "merge" ]]; then
	exit 0
fi
if [[ "\$1" == "pr" && "\$2" == "view" ]]; then
	echo "\${GH_STATES:-OPEN}"
	exit 0
fi
if [[ "\$1" == "api" && "\$2" == "graphql" ]]; then
	echo '{"data":{"createCommitOnBranch":{"commit":{"oid":"created0000000000000000000000000000000a"}}}}'
	exit 0
fi
if [[ "\$1" == "api" && "\$2" == "-X" && "\$3" == "DELETE" ]]; then
	exit \${GH_DELETE_REF_RC:-0}
fi
if [[ "\$1" == "api" && "\$2" == "-X" && "\$3" == "POST" && "\$*" == *"repos/lgtm-hq/lintro-pre-commit/git/refs"* ]]; then
	exit \${GH_CREATE_REF_RC:-0}
fi
exit 0
STUB
	chmod +x "${STUB_BIN}/gh"

	# Stub git: the script only needs fetch/rev-parse/checkout/reset/tag and
	# ls-remote answers, all faked; the mirror checkout itself is a plain dir
	# with a changed pyproject.toml so the bump path runs.
	GIT_LOG="${BATS_TEST_TMPDIR}/git.log"
	: >"${GIT_LOG}"
	cat >"${STUB_BIN}/git" <<STUB
#!/usr/bin/env bash
echo "git \$*" >>"${GIT_LOG}"
if [[ "\$1" == "diff" ]]; then
	# Always report a change so the bump path (branch + PR + merge) runs.
	exit 1
fi
if [[ "\$*" == "ls-remote --tags origin refs/tags/v1.2.3" ]]; then
	exit 1
fi
if [[ "\$*" == "ls-remote --heads origin mirror/bump-lintro-1.2.3" ]]; then
	[[ "\${GIT_BRANCH_EXISTS:-0}" == "1" ]] && echo "ref" || exit 1
	exit 0
fi
if [[ "\$*" == "rev-parse origin/main" ]]; then
	echo "base000000000000000000000000000000000b"
	exit 0
fi
exit 0
STUB
	chmod +x "${STUB_BIN}/git"

	# Stub jq passthrough (real jq is fine; keep PATH simple instead).
	MIRROR_DIR="${BATS_TEST_TMPDIR}/mirror"
	mkdir -p "${MIRROR_DIR}"
	printf '[project]\ndependencies = ["lintro==1.2.2"]\n' >"${MIRROR_DIR}/pyproject.toml"
	# bump_pin.py rewrites the pin; the pyproject now differs from 1.2.3 so
	# the bump path (branch + PR + merge + tag) runs.
	printf '[project]\ndependencies = ["lintro==1.2.2"]\n' >"${MIRROR_DIR}/pyproject.toml"
}

run_script() {
	env PATH="${STUB_BIN}:${PATH}" \
		GH_TOKEN=stub \
		GH_STATES="${GH_STATES:-OPEN}" \
		GIT_BRANCH_EXISTS="${GIT_BRANCH_EXISTS:-0}" \
		MIRROR_DIR="${MIRROR_DIR}" \
		MERGE_TIMEOUT_SECONDS="${MERGE_TIMEOUT_SECONDS:-5}" \
		MERGE_POLL_SECONDS="${MERGE_POLL_SECONDS:-1}" \
		BUMP_SCRIPT="${PROJECT_ROOT}/scripts/ci/mirror/bump_pin.py" \
		bash "$SCRIPT" 1.2.3
}

@test "enables auto-merge then reports MERGED within the bound" {
	GH_STATES="MERGED"
	run run_script
	assert_success
	assert_output --partial "Enabling auto-merge (squash) for mirror PR #22"
	assert_output --partial "Mirror PR #22 merged"
	assert_output --partial "Mirror release v1.2.3 published (PR #22)"

	run grep -F "pr merge 22 --squash --delete-branch --auto" "${GH_LOG}"
	assert_success
}

@test "createCommitOnBranch is called and plain git commit is not" {
	GH_STATES="MERGED"
	run run_script
	assert_success

	run grep -F "api graphql" "${GH_LOG}"
	assert_success

	run grep -F "git commit" "${GIT_LOG}"
	assert_failure
}

@test "a merge-poll timeout exits non-zero and prints the PR URL" {
	GH_STATES="OPEN"
	MERGE_TIMEOUT_SECONDS="2"
	MERGE_POLL_SECONDS="1"
	run run_script
	assert_failure
	assert_output --partial "did not merge within 2s"
	assert_output --partial "https://github.com/lgtm-hq/lintro-pre-commit/pull/22"
}

@test "a stale bump branch is deleted before the new commit is created" {
	GIT_BRANCH_EXISTS="1"
	GH_STATES="MERGED"
	run run_script
	assert_success
	assert_output --partial "recreating it from origin/main"

	run grep -F "git/refs/heads/mirror/bump-lintro-1.2.3" "${GH_LOG}"
	assert_success
}

@test "the bump branch ref is created at base before createCommitOnBranch" {
	# createCommitOnBranch appends to an existing branch; the script must
	# POST refs/heads/<branch> at the base oid first (fresh and heal runs).
	GH_STATES="MERGED"
	run run_script
	assert_success

	run grep -F 'api -X POST repos/lgtm-hq/lintro-pre-commit/git/refs -f ref=refs/heads/mirror/bump-lintro-1.2.3 -f sha=base000000000000000000000000000000000b' "${GH_LOG}"
	assert_success
}

@test "no rebase anywhere in the flow" {
	GH_STATES="MERGED"
	run run_script
	assert_success

	run grep -F "rebase" "${GIT_LOG}"
	assert_failure
}
