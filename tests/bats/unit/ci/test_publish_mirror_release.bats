#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/mirror/publish-mirror-release.sh (#2742)
#
# The script drives git, gh and the GraphQL API against a real mirror
# checkout, so these tests pin its control flow with stubbed gh/git
# behaviour: the bump commit is created via createCommitOnBranch with a
# correctly wrapped GraphQL payload, the auto-merge precondition runs
# before any mirror write, `pr create` fires exactly once per fresh run,
# the merge poll handles OPEN → MERGED and fails fast on closed/dirty PRs,
# a healthy open PR is reused instead of healed, and a stale branch is
# deleted and recreated — never plain git commit or rebase.

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/mirror/publish-mirror-release.sh"

setup() {
	setup_temp_dir

	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	mkdir -p "${STUB_BIN}"

	# Stub gh: records every invocation, answers the PR state poll from
	# GH_STATES (one state per poll, last one repeats), fakes `pr list` /
	# `pr view` / `pr create` / `pr merge`, and CAPTURES the GraphQL payload
	# to graphql.json instead of dropping stdin.
	GH_LOG="${BATS_TEST_TMPDIR}/gh.log"
	GRAPHQL_JSON="${BATS_TEST_TMPDIR}/graphql.json"
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
if [[ "\$1" == "api" && "\$2" == "graphql" ]]; then
	# Consume stdin (a real gh does too — dropping it EPIPEs the producer
	# under Linux pipefail) and persist the payload for assertions.
	cat >"${GRAPHQL_JSON}"
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
		MERGE_TIMEOUT_SECONDS="${MERGE_TIMEOUT_SECONDS:-5}" \
		MERGE_POLL_SECONDS="${MERGE_POLL_SECONDS:-1}" \
		BUMP_SCRIPT="${PROJECT_ROOT}/scripts/ci/mirror/bump_pin.py" \
		bash "$SCRIPT" 1.2.3
}

assert_graphql_payload() {
	# The mutation input must travel under `variables.input` (a raw GraphQL
	# HTTP body delivers variables there; a top-level `input:` leaves $input
	# unbound and the mutation is rejected — the ceabc852 regression).
	run jq -r '.variables.input.expectedHeadOid' "$GRAPHQL_JSON"
	assert_success
	assert_output "base000000000000000000000000000000000b"

	run jq -r '.variables.input.branch.branchName' "$GRAPHQL_JSON"
	assert_success
	assert_output "mirror/bump-lintro-1.2.3"

	run jq -r '.variables.input.branch.repositoryNameWithOwner' "$GRAPHQL_JSON"
	assert_success
	assert_output "lgtm-hq/lintro-pre-commit"

	run jq -r '.variables.input.fileChanges.additions[0].path' "$GRAPHQL_JSON"
	assert_success
	assert_output "pyproject.toml"

	run bash -c "jq -r '.variables.input.fileChanges.additions[0].contents' '$GRAPHQL_JSON' | base64 --decode"
	assert_success
	assert_output '[project]
dependencies = ["lintro==1.2.3"]'
}

@test "createCommitOnBranch receives a variables-wrapped, correct payload" {
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success

	run grep -F "api graphql" "${GH_LOG}"
	assert_success

	assert_graphql_payload

	run grep -F "git commit" "${GIT_LOG}"
	assert_failure
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
	# ref POST, no signed commit, no PR — nothing is written for a run that
	# cannot merge.
	run grep -cF "api -X DELETE" "${GH_LOG}"
	assert_output 0
	run grep -cF "git/refs" "${GH_LOG}"
	assert_output 0
	run grep -cF "api graphql" "${GH_LOG}"
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

@test "the bump branch ref is created at base before createCommitOnBranch" {
	# createCommitOnBranch appends to an existing branch; the script must
	# POST refs/heads/<branch> at the base oid first (fresh and heal runs).
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success

	run grep -F 'api -X POST repos/lgtm-hq/lintro-pre-commit/git/refs -f ref=refs/heads/mirror/bump-lintro-1.2.3 -f sha=base000000000000000000000000000000000b' "${GH_LOG}"
	assert_success

	# And the whole flow is ordered: DELETE (heal, if any) → POST ref →
	# graphql mutation.
	run awk '/graphql/{g=NR}
		/git\/refs -f ref=/{p=NR}
		END {exit !(p && g && p < g)}' "${GH_LOG}"
	assert_success
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
	run grep -cF "api graphql" "${GH_LOG}"
	assert_output 0
	run grep -cF "pr create" "${GH_LOG}"
	assert_output 0
	run grep -cF "api -X DELETE repos/lgtm-hq/lintro-pre-commit/git/refs/heads/mirror/bump-lintro-1.2.3" "${GH_LOG}"
	# finish_after_merge deletes the merged branch ref exactly once.
	assert_output 1
}

@test "a dirty open bump PR is healed: branch deleted, ref recreated, mutation ordered" {
	GIT_BRANCH_EXISTS="1"
	GH_OPEN_PR="1"
	GH_PR_VIEW="OPEN DIRTY main"
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "is not mergeable (OPEN DIRTY main); healing the branch"

	run grep -F 'api -X DELETE repos/lgtm-hq/lintro-pre-commit/git/refs/heads/mirror/bump-lintro-1.2.3' "${GH_LOG}"
	assert_success
	run grep -F 'api -X POST repos/lgtm-hq/lintro-pre-commit/git/refs' "${GH_LOG}"
	assert_success
	run grep -F "api graphql" "${GH_LOG}"
	assert_success

	# DELETE → POST → graphql ordering (first occurrence of each).
	run awk '
		/-X DELETE .*git\/refs\/heads/ && !d { d = NR }
		/git\/refs -f ref=/ && !p { p = NR }
		/graphql/ && !g { g = NR }
		END { exit !(d && p && g && d < p && p < g) }' "${GH_LOG}"
	assert_success

	assert_graphql_payload
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

@test "a stale branch with no open PR is deleted before the new commit is created" {
	GIT_BRANCH_EXISTS="1"
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success
	assert_output --partial "exists with no open PR; recreating it from origin/main"

	run grep -F "git/refs/heads/mirror/bump-lintro-1.2.3" "${GH_LOG}"
	assert_success
}

@test "no rebase anywhere in the flow" {
	GH_STATES="MERGED MERGED"
	run run_script
	assert_success

	run grep -F "rebase" "${GIT_LOG}"
	assert_failure
}
