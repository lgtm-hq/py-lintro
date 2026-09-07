#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: E404 retry classification in scripts/ci/npm/publish_packages.sh
# (issue #2247). npm masks an unauthorized publish as a 404, so publish E404
# must be fatal, while the `npm view` pre-check keeps reading E404 as
# "this version is not published yet".

load "../../helpers/common"

SCRIPT="${NPM_SCRIPTS_DIR}/publish_packages.sh"

setup() {
	setup_temp_dir
	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	ATTEMPT_LOG="${BATS_TEST_TMPDIR}/publish-attempts.log"
	make_stub_path "$STUB_BIN" bash node grep sed sleep dirname mktemp wc tr >/dev/null
	export ATTEMPT_LOG
	export NPM_PUBLISH_RETRY_DELAY=0
	export NPM_PROVENANCE=0
}

teardown() {
	teardown_temp_dir
}

# Install a fake `npm` whose publish failure text is read from a file. `npm
# view` always reports E404 (version absent), so the loop proceeds to publish.
# The stub uses only shell builtins so it runs under the restricted stub PATH.
_install_fake_npm() {
	printf '%s\n' "$1" >"${BATS_TEST_TMPDIR}/publish-error.txt"
	cat >"${STUB_BIN}/npm" <<'EOF'
#!/usr/bin/env bash
case "$1" in
view)
	echo "npm error code E404" >&2
	echo "npm error 404 Not Found - GET https://registry.npmjs.org/pkg" >&2
	exit 1
	;;
publish)
	echo "publish" >>"$ATTEMPT_LOG"
	while IFS= read -r line; do
		printf '%s\n' "$line" >&2
	done <"$NPM_PUBLISH_ERROR_FILE"
	exit 1
	;;
*)
	echo "unexpected npm invocation: $*" >&2
	exit 99
	;;
esac
EOF
	chmod +x "${STUB_BIN}/npm"
	export NPM_PUBLISH_ERROR_FILE="${BATS_TEST_TMPDIR}/publish-error.txt"
}

_attempt_count() {
	if [[ -f "$ATTEMPT_LOG" ]]; then
		wc -l <"$ATTEMPT_LOG" | tr -d ' '
	else
		echo 0
	fi
}

@test "publish_packages.sh: an E404 publish failure is never retried" {
	_install_fake_npm "npm error code E404
npm error 404 '@lgtm-hq/lintro-darwin-arm64@0.0.0-dev' could not be found or you do not have permission to access it."

	PATH="$STUB_BIN" LIVE=1 run "$SCRIPT"
	assert_failure
	assert_output --partial "non-retryable"
	assert_equal "1" "$(_attempt_count)"
}

@test "publish_packages.sh: a transient failure is still retried" {
	_install_fake_npm "npm error code ETIMEDOUT
npm error network request to https://registry.npmjs.org failed"

	PATH="$STUB_BIN" LIVE=1 run "$SCRIPT"
	assert_failure
	assert_output --partial "transient publish error"
	assert_equal "3" "$(_attempt_count)"
}

@test "publish_packages.sh: E404 from the npm view pre-check still means unpublished" {
	# The pre-check must keep treating E404 as "not published yet" — it must
	# not warn about an unverifiable lookup — and then publish.
	_install_fake_npm "npm error code E404
npm error 404 not found"

	PATH="$STUB_BIN" LIVE=1 run "$SCRIPT"
	assert_failure
	[[ "$output" != *"could not verify"* ]] || {
		echo "# view E404 must not be reported as an unverifiable lookup" >&2
		echo "# Output: ${output}" >&2
		return 1
	}
}
