#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: read-before-write dist-tag reconcile on a live re-run of
# scripts/ci/npm/publish_packages.sh (issue #2631). Under OIDC trusted
# publishing the token cannot run `npm dist-tag add` (npm/cli#8547), so a
# re-run over already-published packages must detect "tag already correct"
# with `npm dist-tag ls` and write nothing.

load "../../helpers/common"

SCRIPT="${NPM_SCRIPTS_DIR}/publish_packages.sh"

setup() {
	setup_temp_dir
	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	PUBLISH_LOG="${BATS_TEST_TMPDIR}/publish.log"
	DIST_TAG_LOG="${BATS_TEST_TMPDIR}/dist-tag.log"
	DIST_TAG_LS_LOG="${BATS_TEST_TMPDIR}/dist-tag-ls.log"
	make_stub_path "$STUB_BIN" bash node grep sed awk sleep dirname mktemp wc tr >/dev/null
	export PUBLISH_LOG
	export DIST_TAG_LOG
	export DIST_TAG_LS_LOG
	export NPM_PUBLISH_RETRY_DELAY=0
	export NPM_PROVENANCE=0
}

teardown() {
	teardown_temp_dir
}

# Install a fake `npm` where every package is already published with its
# dist-tag already correct: `npm view` reports the version, `npm dist-tag ls`
# lists `latest: 9.9.9`, and `npm dist-tag add` fails like the registry does
# under a publish-scoped OIDC token — the run must never reach it.
_install_fake_npm() {
	cat >"${STUB_BIN}/npm" <<'EOF'
#!/usr/bin/env bash
case "$1" in
view)
	echo "9.9.9"
	exit 0
	;;
dist-tag)
	if [[ "$2" == "ls" ]]; then
		echo "dist-tag $*" >>"$DIST_TAG_LS_LOG"
		# The version the repo's real npm/ manifests carry.
		echo "latest: 0.0.0-dev"
		exit 0
	fi
	echo "dist-tag $*" >>"$DIST_TAG_LOG"
	echo "npm error code E403" >&2
	echo "npm error 403 Forbidden - PUT registry/-/package/dist-tags" >&2
	exit 1
	;;
publish)
	echo "publish" >>"$PUBLISH_LOG"
	exit 0
	;;
*)
	echo "unexpected npm invocation: $*" >&2
	exit 99
	;;
esac
EOF
	chmod +x "${STUB_BIN}/npm"
}

@test "publish_packages.sh: a live re-run over published versions writes no dist-tag" {
	_install_fake_npm

	PATH="$STUB_BIN" LIVE=1 run "$SCRIPT"
	assert_success
	assert_output --partial "already points at"
	assert_output --partial "Skipping @lgtm-hq/lintro-darwin-arm64"
	# Every already-published package was skipped, none re-published.
	if [[ -f "$PUBLISH_LOG" ]]; then
		fail "packages were re-published: $(cat "$PUBLISH_LOG")"
	fi
	# The read happened once per package...
	ls_count="$(grep -c 'dist-tag ls' "$DIST_TAG_LS_LOG" 2>/dev/null || echo 0)"
	assert_equal "4" "$ls_count"
	# ...and the OIDC-doomed write never did.
	if [[ -f "$DIST_TAG_LOG" ]]; then
		fail "npm dist-tag add was invoked: $(cat "$DIST_TAG_LOG")"
	fi
}
