#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/release-gate/read_manifest_sha.sh (#2562)

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/release-gate/read_manifest_sha.sh"
ARM64_SHA="aaaa111111111111111111111111111111111111111111111111111111111111"

setup() {
	setup_temp_dir
	setup_github_env
	MANIFEST="${BATS_TEST_TMPDIR}/release-manifest.json"
	cat >"${MANIFEST}" <<EOF
{
  "schema": 1,
  "tag": "v1.2.3",
  "files": {
    "lintro-macos-arm64": {"sha256": "${ARM64_SHA}", "size": 12, "bundle": "lintro-macos-arm64.intoto.jsonl"},
    "lintro.1": {"sha256": "bad", "size": 1, "bundle": null}
  }
}
EOF
}

@test "prints the asset's sha256 and exports arm64_sha256" {
	run "$SCRIPT" "${MANIFEST}" lintro-macos-arm64
	assert_success
	assert_output "${ARM64_SHA}"
	run get_github_output arm64_sha256
	assert_output "${ARM64_SHA}"
}

@test "fails when the asset is missing from the manifest" {
	run "$SCRIPT" "${MANIFEST}" lintro-linux-x64
	assert_failure
	assert_output --partial "no sha256 for lintro-linux-x64"
	run cat "${GITHUB_OUTPUT}"
	assert_output ""
}

@test "fails on a malformed digest" {
	run "$SCRIPT" "${MANIFEST}" lintro.1
	assert_failure
	assert_output --partial "no sha256 for lintro.1"
}

@test "fails when the manifest is absent" {
	run "$SCRIPT" "${BATS_TEST_TMPDIR}/nope.json" lintro-macos-arm64
	assert_failure
	assert_output --partial "manifest not found or empty"
}
