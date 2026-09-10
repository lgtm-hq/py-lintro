#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: promote-ci-docker-images.sh honours the staleness guard (#2497)

load "../../helpers/common"

CI_SCRIPTS_DIR="${PROJECT_ROOT}/scripts/ci"

setup() {
	setup_temp_dir
	SCRIPT="${CI_SCRIPTS_DIR}/promote-ci-docker-images.sh"
	CANDIDATE_PR_NUMBER=7

	# Record every registry call so a refusal can be shown to mutate nothing.
	DOCKER_LOG="${BATS_TEST_TMPDIR}/docker.log"
	: >"${DOCKER_LOG}"
	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	mkdir -p "${STUB_BIN}"
	cat >"${STUB_BIN}/docker" <<STUB
#!/usr/bin/env bash
echo "\$*" >>"${DOCKER_LOG}"
if [[ "\$*" == *" inspect "* ]]; then
	echo "sha256:aaa111"
fi
STUB
	chmod +x "${STUB_BIN}/docker"

	UPSTREAM="${BATS_TEST_TMPDIR}/upstream.git"
	git init --quiet --bare "$UPSTREAM"
	SEED="${BATS_TEST_TMPDIR}/seed"
	mkdir -p "${SEED}/lintro"
	cd "${SEED}" || return 1
	git init --quiet --initial-branch=main .
	git config user.email 'test@example.com'
	git config user.name 'Test'
	printf 'RUFF = "0.1.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'base'
	git checkout --quiet -b candidate
	printf 'RUFF = "0.2.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'bump ruff'
	CANDIDATE_FULL="$(git rev-parse HEAD)"
	CANDIDATE_ABBREV="${CANDIDATE_FULL:0:12}"
	git checkout --quiet main
	printf 'RUFF = "0.2.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'chore(deps): bump ruff (#1)'
	git push --quiet "$UPSTREAM" main
	git push --quiet "$UPSTREAM" "candidate:refs/pull/${CANDIDATE_PR_NUMBER}/head"

	REPO="${BATS_TEST_TMPDIR}/repo"
	git clone --quiet --no-local "$UPSTREAM" "$REPO"
	cd "${REPO}" || return 1
	git config user.email 'test@example.com'
	git config user.name 'Test'
}

run_promote() {
	env PATH="${STUB_BIN}:${PATH}" \
		SOURCE_IMAGE=ghcr.io/lgtm-hq/lintro-tools \
		CI_TAG="tools-candidate-pr${CANDIDATE_PR_NUMBER}-${CANDIDATE_ABBREV}" \
		TAGS=ghcr.io/lgtm-hq/lintro-tools:latest \
		CANDIDATE_SHA="${CANDIDATE_ABBREV}" \
		CANDIDATE_PR="${CANDIDATE_PR_NUMBER}" \
		MAIN_SHA="$(git rev-parse main)" \
		CANDIDATE_FETCH_DELAY_SECONDS=0 \
		GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary" \
		"$@" \
		"$SCRIPT"
}

@test "a stale candidate is refused before any registry call" {
	printf 'RUFF = "0.3.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'chore(deps): bump ruff again (#2)'

	run run_promote env
	assert_failure
	assert_output --partial "refusing to promote: main has newer tool manifest commits"

	# Nothing was inspected, retagged, or verified.
	run cat "${DOCKER_LOG}"
	assert_output ""
}

@test "a current candidate is promoted by digest" {
	run run_promote env
	assert_success

	run cat "${DOCKER_LOG}"
	assert_output --partial "buildx imagetools create --prefer-index=false"
	assert_output --partial "ghcr.io/lgtm-hq/lintro-tools@sha256:aaa111"
	assert_output --partial "--tag ghcr.io/lgtm-hq/lintro-tools:latest"
}

@test "force_publish promotes a stale candidate and says so" {
	printf 'RUFF = "0.3.0"\n' >lintro/_tool_versions.py
	git add -A
	git commit --quiet -m 'chore(deps): bump ruff again (#2)'

	run run_promote env FORCE_PUBLISH=true
	assert_success
	assert_output --partial "force_publish=true"

	run cat "${DOCKER_LOG}"
	assert_output --partial "buildx imagetools create"
}
