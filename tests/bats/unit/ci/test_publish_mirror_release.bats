#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/mirror/publish-mirror-release.sh (#2742)
#
# The script drives git, gh and lgtm-ci's shared create-signed-commit
# script against a real mirror checkout, so these tests pin its control
# flow with stubbed gh/git and a stub shared script under a fake
# LGTM_CI_TOOLING_DIR: the bump commit is made by the shared script in
# reset mode with the expected arguments, run from the mirror checkout
# (#2834); the auto-merge precondition runs before any mirror write;
# `pr create` fires exactly once per fresh run; the merge poll handles
# OPEN → MERGED and fails fast on closed/dirty PRs; a healthy open PR is
# reused instead of healed; an unmergeable PR's branch is deleted before
# the reset; and a stale branch with no PR is reset in place — never plain
# git commit or rebase.

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/mirror/publish-mirror-release.sh"

setup() {
	setup_temp_dir

	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	mkdir -p "${STUB_BIN}"

	# Stub gh: records every invocation, answers the PR state poll from
	# GH_STATES (one state per poll, last one repeats), and fakes `pr list`
	# / `pr view` / `pr create` / `pr merge`.
	GH_LOG="${BATS_TEST_TMPDIR}/gh.log"
	: >"${GH_LOG}"
	cat >"${STUB_BIN}/gh" <<STUB
#!/usr/bin/env bash
echo "\$*" >>"${GH_LOG}"
if [[ "\$1" == "pr" && "\$2" == "list" ]]; then
	if [[ "\${GH_OPEN_PR:-0}" == "1" ]]; then
		echo "22"
	else
		echo ""
	fi
	exit 0
fi
if [[ "\$1" == "pr" && "\$2" == "view" ]]; then
	case "\$*" in
	*autoMergeRequest*)
		echo "\${GH_AUTO_ARMED:-false}"
		;;
	*baseRefName*)
		# Reuse-path guard: "state mergeState base" ("UNKNOWN" fills an
		# empty mergeStateStatus, as the script's jq fallback does).
		printf '%s\n' "\${GH_PR_VIEW:-OPEN CLEAN main}"
		;;
	*)
		# Merge poll: "state mergeState" per poll from GH_STATES, last one
		# repeats; the poll index lives in a file because each gh call is a
		# fresh process.
		POLL_N="${BATS_TEST_TMPDIR}/poll.n"
		touch "\${POLL_N}"
		poll=\$(cat "\${POLL_N}")
		n=\$(printf '%s\n' "\$GH_STATES" | tr ',' '\\n' | wc -l)
		if [[ "\$poll" -lt "\$n" ]]; then
			poll=\$((poll + 1))
			echo "\$poll" >"\${POLL_N}"
		fi
		printf '%s\n' "\$GH_STATES" | tr ',' '\\n' | sed -n "\${poll}p"
		;;
	esac
	exit 0
fi
if [[ "\$1" == "pr" && "\$2" == "create" ]]; then
	# Stateful: a second create for the same head fails like GitHub does
	# ("a pull request for branch ... already exists") — the fresh-path
	# double-create of the 8afbdd2f regression dies here under set -e.
	# argv: pr create --base main --head <branch> --title ... --body ...
	CREATED="${BATS_TEST_TMPDIR}/created.heads"
	if [[ -f "\${CREATED}" ]] && grep -qxF "\$6" "\${CREATED}"; then
		echo "creating pull request failed: a pull request for branch \$6 already exists" >&2
		exit 1
	fi
	echo "\$6" >>"\${CREATED}"
	echo "https://github.com/lgtm-hq/lintro-pre-commit/pull/22"
	exit 0
fi
if [[ "\$1" == "pr" && "\$2" == "merge" ]]; then
	exit \${GH_PR_MERGE_RC:-0}
fi
if [[ "\$1" == "api" && "\$2" == "repos/lgtm-hq/lintro-pre-commit" && "\$3" == "--jq" ]]; then
	echo "\${GH_ALLOW_AUTO_MERGE:-true}"
	exit 0
fi
if [[ "\$1" == "api" && "\$2" == "-X" && "\$3" == "DELETE" ]]; then
	exit \${GH_DELETE_REF_RC:-0}
fi
exit 0
STUB
	chmod +x "${STUB_BIN}/gh"

	# Stub lgtm-ci's create-signed-commit.sh under a fake tooling checkout:
	# it logs a marker line into gh.log (so ordering against gh calls can be
	# asserted), records its cwd and flag=value pairs (the multi-line --body
	# is only marked present), snapshots ./pyproject.toml as read from its
	# cwd, and prints the commit-sha= line the real script prints.
	LGTM_CI_TOOLING_DIR="${BATS_TEST_TMPDIR}/lgtm-ci-tooling"
	mkdir -p "${LGTM_CI_TOOLING_DIR}/scripts/ci/git"
	SIGNED_ARGS="${BATS_TEST_TMPDIR}/signed-commit.args"
	SIGNED_PYPROJECT="${BATS_TEST_TMPDIR}/signed-commit.pyproject"
	cat >"${LGTM_CI_TOOLING_DIR}/scripts/ci/git/create-signed-commit.sh" <<STUB
#!/usr/bin/env bash
echo "create-signed-commit" >>"${GH_LOG}"
{
	printf 'cwd=%s\\n' "\$PWD"
	while [[ \$# -gt 0 ]]; do
		if [[ "\$1" == "--body" ]]; then
			printf '%s\\n' "--body=<set>"
		else
			printf '%s=%s\\n' "\$1" "\$2"
		fi
		shift 2
	done
} >>"${SIGNED_ARGS}"
cp pyproject.toml "${SIGNED_PYPROJECT}"
if [[ "\${SIGNED_COMMIT_RC:-0}" != "0" ]]; then
	echo "[ERROR] createCommitOnBranch returned no commit: stub failure" >&2
	exit "\${SIGNED_COMMIT_RC}"
fi
echo "commit-sha=created0000000000000000000000000000000a"
echo "commit-url=https://github.com/lgtm-hq/lintro-pre-commit/commit/created0000000000000000000000000000000a"
STUB

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

	MIRROR_DIR="${BATS_TEST_TMPDIR}/mirror"
	mkdir -p "${MIRROR_DIR}"
	# bump_pin.py rewrites the pin; the pyproject now differs from 1.2.3 so
	# the bump path (branch + PR + merge + tag) runs.
	printf '[project]\ndependencies = ["lintro==1.2.2"]\n' >"${MIRROR_DIR}/pyproject.toml"
}

run_script() {
	env PATH="${STUB_BIN}:${PATH}" \
		GH_TOKEN=stub \
		GH_STATES="${GH_STATES:-MERGED MERGED}" \
		GH_OPEN_PR="${GH_OPEN_PR:-0}" \
		GH_PR_VIEW="${GH_PR_VIEW:-OPEN CLEAN main}" \
		GH_AUTO_ARMED="${GH_AUTO_ARMED:-false}" \
		GH_ALLOW_AUTO_MERGE="${GH_ALLOW_AUTO_MERGE:-true}" \
		GIT_BRANCH_EXISTS="${GIT_BRANCH_EXISTS:-0}" \
		MIRROR_DIR="${MIRROR_DIR}" \
		LGTM_CI_TOOLING_DIR="${LGTM_CI_TOOLING_DIR}" \
		SIGNED_COMMIT_RC="${SIGNED_COMMIT_RC:-0}" \
		MERGE_TIMEOUT_SECONDS="${MERGE_TIMEOUT_SECONDS:-5}" \
		MERGE_POLL_SECONDS="${MERGE_POLL_SECONDS:-1}" \
		BUMP_SCRIPT="${PROJECT_ROOT}/scripts/ci/mirror/bump_pin.py" \
		bash "$SCRIPT" 1.2.3
}

assert_signed_commit_args() {
	# The shared script gets reset mode onto the synced main, the bump
	# branch, the mirror repo, the existing headline and the pin file, and
	# runs from the mirror checkout so --file pyproject.toml resolves there.
	local expected
	for expected in \
		"cwd=${MIRROR_DIR}" \
		"--mode=reset" \
		"--base=base000000000000000000000000000000000b" \
		"--branch=mirror/bump-lintro-1.2.3" \
		"--repository=lgtm-hq/lintro-pre-commit" \
		"--message=chore: bump lintro to 1.2.3" \
		"--body=<set>" \
		"--file=pyproject.toml"; do
		run grep -cxF -- "$expected" "$SIGNED_ARGS"
		assert_success
		assert_output 1
	done

	# The file it uploads is the bumped pin.
	run cat "$SIGNED_PYPROJECT"
	assert_success
	assert_output '[project]
dependencies = ["lintro==1.2.3"]'
}

@test "the shared create-signed-commit script makes the bump commit in reset mode" {
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "Created bump commit created0000000000000000000000000000000a"

	run grep -cxF "create-signed-commit" "${GH_LOG}"
	assert_output 1

	assert_signed_commit_args

	# No inline mutation or ref POST is left in the script's own calls.
	run grep -F "api graphql" "${GH_LOG}"
	assert_failure
	run grep -F "api -X POST" "${GH_LOG}"
	assert_failure
	run grep -F "git commit" "${GIT_LOG}"
	assert_failure
}

@test "a missing LGTM_CI_TOOLING_DIR fails before any mirror write" {
	LGTM_CI_TOOLING_DIR="${BATS_TEST_TMPDIR}/no-such-tooling"
	run run_script
	assert_failure
	assert_output --partial "create-signed-commit script not found at ${LGTM_CI_TOOLING_DIR}/scripts/ci/git/create-signed-commit.sh"

	# Not even the auto-merge setting read reaches the mirror.
	run cat "${GH_LOG}"
	assert_output ""
}

@test "a failed signed commit stops the run before any PR is opened" {
	SIGNED_COMMIT_RC="1"
	run run_script
	assert_failure
	assert_output --partial "createCommitOnBranch returned no commit"

	run grep -cF "pr create" "${GH_LOG}"
	assert_output 0
	run grep -cF "pr merge" "${GH_LOG}"
	assert_output 0
}

@test "the fresh path opens exactly one PR (stateful create stub)" {
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success

	# Exactly one `pr create`: the stub refuses a second create for the same
	# head with GitHub's "already exists" error, so this fails on the
	# 8afbdd2f double-create regression — which the fixed-URL stub hid.
	run grep -cF "pr create" "${GH_LOG}"
	assert_success
	assert_output 1
}

@test "enables auto-merge after checking the repo setting, then reports MERGED" {
	GH_STATES="OPEN OPEN,MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "Enabling auto-merge (squash) for mirror PR #22"
	assert_output --partial "Mirror PR #22 merged"
	assert_output --partial "Mirror release v1.2.3 published (PR #22)"

	# The precondition must precede the merge in the gh call log.
	run awk '/repos\/lgtm-hq\/lintro-pre-commit --jq .allow_auto_merge/{pre=NR}
		/pr merge 22 --squash --auto/ && !seen {if (pre && NR > pre) ok=1; seen=1}
		END {exit !(ok)}' "${GH_LOG}"
	assert_success
}

@test "already-armed auto-merge is not re-armed on the reuse path" {
	GIT_BRANCH_EXISTS="1"
	GH_OPEN_PR="1"
	GH_PR_VIEW="OPEN CLEAN main"
	GH_AUTO_ARMED="true"
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "Auto-merge already armed for mirror PR #22"

	run grep -cF "pr merge" "${GH_LOG}"
	assert_output 0
}

@test "auto-merge disabled on the mirror fails before any mirror write" {
	GH_ALLOW_AUTO_MERGE="false"
	run run_script
	assert_failure
	assert_output --partial "Auto-merge is disabled on lgtm-hq/lintro-pre-commit"
	assert_output --partial "Allow auto-merge"

	# The precondition runs before ANY mirror mutation: no branch heal, no
	# signed commit (and so no temp branch), no PR — nothing is written for
	# a run that cannot merge.
	run grep -cF "api -X DELETE" "${GH_LOG}"
	assert_output 0
	run grep -cF "git/refs" "${GH_LOG}"
	assert_output 0
	run grep -cxF "create-signed-commit" "${GH_LOG}"
	assert_output 0
	run grep -cF "pr create" "${GH_LOG}"
	assert_output 0
	run grep -cF "pr merge" "${GH_LOG}"
	assert_output 0
}

@test "a merge-poll timeout exits non-zero and prints the PR URL" {
	GH_STATES="OPEN OPEN,OPEN OPEN,OPEN OPEN,OPEN OPEN"
	MERGE_TIMEOUT_SECONDS="2"
	MERGE_POLL_SECONDS="1"
	run run_script
	assert_failure
	assert_output --partial "did not merge within 2s"
	assert_output --partial "https://github.com/lgtm-hq/lintro-pre-commit/pull/22"
}

@test "a PR closed without merging stops the poll early" {
	GH_STATES="OPEN OPEN,CLOSED CLOSED"
	run run_script
	assert_failure
	assert_output --partial "was closed without merging"
	assert_output --partial "https://github.com/lgtm-hq/lintro-pre-commit/pull/22"
}

@test "a DIRTY merge state stops the poll early with the PR URL" {
	GH_STATES="OPEN OPEN,OPEN DIRTY"
	run run_script
	assert_failure
	assert_output --partial "is DIRTY (merge conflict)"
	assert_output --partial "https://github.com/lgtm-hq/lintro-pre-commit/pull/22"
}

@test "a healthy open bump PR is reused instead of healed" {
	GIT_BRANCH_EXISTS="1"
	GH_OPEN_PR="1"
	GH_PR_VIEW="OPEN BLOCKED main"
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "Reusing open PR #22 for mirror/bump-lintro-1.2.3"
	assert_output --partial "Mirror PR #22 merged"

	# No heal, no new commit, no new PR on the reuse path.
	run grep -cxF "create-signed-commit" "${GH_LOG}"
	assert_output 0
	run grep -cF "pr create" "${GH_LOG}"
	assert_output 0
	run grep -cF "api -X DELETE repos/lgtm-hq/lintro-pre-commit/git/refs/heads/mirror/bump-lintro-1.2.3" "${GH_LOG}"
	# finish_after_merge deletes the merged branch ref exactly once.
	assert_output 1
}

@test "a dirty open bump PR is healed: branch deleted, then reset by the shared script" {
	GIT_BRANCH_EXISTS="1"
	GH_OPEN_PR="1"
	GH_PR_VIEW="OPEN DIRTY main"
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "is not mergeable (OPEN DIRTY main); healing the branch"

	# DELETE (closes the unmergeable PR) → shared script reset (first
	# occurrence of each; finish_after_merge deletes the ref again later).
	run awk '
		/-X DELETE .*git\/refs\/heads\/mirror\/bump-lintro-1\.2\.3/ && !d { d = NR }
		/^create-signed-commit$/ && !c { c = NR }
		END { exit !(d && c && d < c) }' "${GH_LOG}"
	assert_success

	assert_signed_commit_args
	run grep -cF "pr create" "${GH_LOG}"
	assert_output 1
}

@test "an open PR against another base is not reused (healed instead)" {
	GIT_BRANCH_EXISTS="1"
	GH_OPEN_PR="1"
	GH_PR_VIEW="OPEN CLEAN other-base"
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "is not mergeable (OPEN CLEAN other-base); healing the branch"

	# The stale branch is deleted and a fresh PR is opened against main.
	run grep -F 'api -X DELETE repos/lgtm-hq/lintro-pre-commit/git/refs/heads/mirror/bump-lintro-1.2.3' "${GH_LOG}"
	assert_success
	run grep -cF "pr create" "${GH_LOG}"
	assert_output 1
}

@test "a stale branch with no open PR is reset in place, not deleted first" {
	GIT_BRANCH_EXISTS="1"
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "exists with no open PR; resetting it onto origin/main"

	# Reset mode moves the branch in one step; the only branch DELETE is
	# finish_after_merge's cleanup, after the signed commit.
	run grep -cF "api -X DELETE repos/lgtm-hq/lintro-pre-commit/git/refs/heads/mirror/bump-lintro-1.2.3" "${GH_LOG}"
	assert_output 1
	run awk '
		/^create-signed-commit$/ && !c { c = NR }
		/-X DELETE .*git\/refs\/heads\/mirror\/bump-lintro-1\.2\.3/ && !d { d = NR }
		END { exit !(c && d && c < d) }' "${GH_LOG}"
	assert_success

	assert_signed_commit_args
}

@test "no rebase anywhere in the flow" {
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success

	run grep -F "rebase" "${GIT_LOG}"
	assert_failure
}
