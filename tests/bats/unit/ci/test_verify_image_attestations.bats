#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/verify-image-attestations.sh (#2562)

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/verify-image-attestations.sh"

FULL_DIGEST="sha256:aaaa111111111111111111111111111111111111111111111111111111111111"
BASE_DIGEST="sha256:bbbb222222222222222222222222222222222222222222222222222222222222"

setup() {
	setup_temp_dir

	# Stubbed gh: records every argv line and fails for any ref listed in
	# GH_FAIL_REFS, so both the happy path and the fail-closed path run
	# without the attestations API.
	GH_LOG="${BATS_TEST_TMPDIR}/gh.log"
	: >"${GH_LOG}"
	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	mkdir -p "${STUB_BIN}"
	cat >"${STUB_BIN}/gh" <<STUB
#!/usr/bin/env bash
echo "\$*" >>"${GH_LOG}"
for bad in \${GH_FAIL_REFS:-}; do
	if [[ "\$*" == *"\$bad"* ]]; then
		echo "no attestation found" >&2
		exit 1
	fi
done
STUB
	chmod +x "${STUB_BIN}/gh"
	export GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary"
}

run_verify() {
	env PATH="${STUB_BIN}:${PATH}" \
		ATTESTATION_REPO=lgtm-hq/py-lintro \
		SIGNER_REPO=lgtm-hq/lgtm-ci \
		"$@" \
		"$SCRIPT"
}

@test "verifies every digest ref with --repo and --signer-repo" {
	run run_verify env IMAGES="ghcr.io/lgtm-hq/py-lintro@${FULL_DIGEST}
ghcr.io/lgtm-hq/py-lintro-base@${BASE_DIGEST}"
	assert_success
	assert_output --partial "Verified attestations for 2 image(s)"

	run cat "${GH_LOG}"
	assert_output "attestation verify oci://ghcr.io/lgtm-hq/py-lintro@${FULL_DIGEST} --repo lgtm-hq/py-lintro --signer-repo lgtm-hq/lgtm-ci
attestation verify oci://ghcr.io/lgtm-hq/py-lintro-base@${BASE_DIGEST} --repo lgtm-hq/py-lintro --signer-repo lgtm-hq/lgtm-ci"

	run cat "${GITHUB_STEP_SUMMARY}"
	assert_output --partial "attestation verified: \`ghcr.io/lgtm-hq/py-lintro@${FULL_DIGEST}\`"
}

@test "a failed verification fails the run" {
	run run_verify env GH_FAIL_REFS="${BASE_DIGEST}" \
		IMAGES="ghcr.io/lgtm-hq/py-lintro@${FULL_DIGEST}
ghcr.io/lgtm-hq/py-lintro-base@${BASE_DIGEST}"
	assert_failure
	[[ "${output}" != *"Verified attestations for"* ]]
}

@test "refuses a floating tag ref before calling gh" {
	run run_verify env IMAGES="ghcr.io/lgtm-hq/py-lintro:latest"
	assert_failure
	assert_output --partial "Refusing to verify non-digest ref"

	run cat "${GH_LOG}"
	assert_output ""
}

@test "requires IMAGES, ATTESTATION_REPO and SIGNER_REPO" {
	run run_verify env IMAGES=
	assert_failure
	assert_output --partial "IMAGES is required"

	run run_verify env IMAGES="ghcr.io/lgtm-hq/py-lintro@${FULL_DIGEST}" ATTESTATION_REPO=
	assert_failure
	assert_output --partial "ATTESTATION_REPO is required"

	run run_verify env IMAGES="ghcr.io/lgtm-hq/py-lintro@${FULL_DIGEST}" SIGNER_REPO=
	assert_failure
	assert_output --partial "SIGNER_REPO is required"
}
