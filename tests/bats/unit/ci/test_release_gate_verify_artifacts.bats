#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/ci/release-gate/verify_artifacts.sh (#2562)

load "../../helpers/common"

SCRIPT="${PROJECT_ROOT}/scripts/ci/release-gate/verify_artifacts.sh"
DIST_SIGNER="lgtm-hq/lgtm-ci/.github/workflows/reusable-build-python-dist.yml"
BIN_SIGNER="lgtm-hq/py-lintro/.github/workflows/build-binaries.yml"

sha256_of() {
	compute_expected_sha256 "$1"
}

setup() {
	setup_temp_dir
	export GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary"

	# Stubbed gh: records every argv line; `attestation verify` fails for
	# any subject named in GH_FAIL_SUBJECTS, `attestation download` writes
	# one bundle into the cwd the way the real CLI does.
	GH_LOG="${BATS_TEST_TMPDIR}/gh.log"
	: >"${GH_LOG}"
	STUB_BIN="${BATS_TEST_TMPDIR}/bin"
	mkdir -p "${STUB_BIN}"
	cat >"${STUB_BIN}/gh" <<STUB
#!/usr/bin/env bash
echo "\$*" >>"${GH_LOG}"
if [[ "\$1 \$2" == "attestation verify" ]]; then
	for bad in \${GH_FAIL_SUBJECTS:-}; do
		if [[ "\$3" == *"\$bad" ]]; then
			echo "no attestation found for \$3" >&2
			exit 1
		fi
	done
	exit 0
fi
if [[ "\$1 \$2" == "attestation download" ]]; then
	printf '{"bundle":"%s"}\n' "\$(basename "\$3")" >"sha256:stub-\$(basename "\$3").jsonl"
	exit 0
fi
exit 1
STUB
	chmod +x "${STUB_BIN}/gh"

	GATE="${BATS_TEST_TMPDIR}/gate"
	mkdir -p "${GATE}/dist" "${GATE}/binaries" "${GATE}/man"
	printf 'sdist bytes\n' >"${GATE}/dist/lintro-1.2.3.tar.gz"
	printf 'wheel bytes\n' >"${GATE}/dist/lintro-1.2.3-py3-none-any.whl"
	(
		cd "${GATE}/dist"
		printf '%s  %s\n' "$(sha256_of lintro-1.2.3.tar.gz)" lintro-1.2.3.tar.gz \
			"$(sha256_of lintro-1.2.3-py3-none-any.whl)" lintro-1.2.3-py3-none-any.whl >SHA256SUMS
	)
	for bin in lintro-macos-arm64 lintro-linux-x64 lintro-linux-arm64; do
		printf 'binary %s\n' "$bin" >"${GATE}/binaries/${bin}"
	done
	printf '.TH LINTRO 1\n' >"${GATE}/man/lintro.1"
	OUT="${BATS_TEST_TMPDIR}/release-assets"
}

run_gate() {
	env PATH="${STUB_BIN}:${PATH}" \
		GATE_DIR="${GATE}" OUT_DIR="${OUT}" \
		ATTESTATION_REPO=lgtm-hq/py-lintro \
		DIST_SIGNER_WORKFLOW="${DIST_SIGNER}" \
		BINARY_SIGNER_WORKFLOW="${BIN_SIGNER}" \
		"$@" \
		"$SCRIPT"
}

@test "verifies every dist file and binary with its own signer workflow and assembles the assets" {
	run run_gate env
	assert_success
	assert_output --partial "Verified 5 attested asset(s)"

	run cat "${GH_LOG}"
	assert_output --partial "attestation verify ${GATE}/dist/lintro-1.2.3.tar.gz --repo lgtm-hq/py-lintro --signer-workflow ${DIST_SIGNER}"
	assert_output --partial "attestation verify ${GATE}/dist/lintro-1.2.3-py3-none-any.whl --repo lgtm-hq/py-lintro --signer-workflow ${DIST_SIGNER}"
	assert_output --partial "attestation verify ${GATE}/binaries/lintro-linux-x64 --repo lgtm-hq/py-lintro --signer-workflow ${BIN_SIGNER}"
	assert_output --partial "attestation download ${GATE}/binaries/lintro-macos-arm64 --repo lgtm-hq/py-lintro"

	# Every asset, its bundle, the man page and one SHA256SUMS over all of it.
	for f in lintro-1.2.3.tar.gz lintro-1.2.3-py3-none-any.whl lintro-macos-arm64 lintro-linux-x64 lintro-linux-arm64; do
		[[ -s "${OUT}/${f}" ]]
		[[ -s "${OUT}/${f}.intoto.jsonl" ]]
	done
	[[ -s "${OUT}/lintro.1" ]]
	run grep -c "" "${OUT}/SHA256SUMS"
	assert_output "11"
	run bash -c "cd '${OUT}' && ( sha256sum -c --strict SHA256SUMS || shasum -a 256 -c --strict SHA256SUMS ) >/dev/null && echo checked"
	assert_output "checked"
	run grep -c "attestation verified" "${GITHUB_STEP_SUMMARY}"
	assert_output "5"
}

@test "a failed attestation fails the gate before anything is assembled" {
	run run_gate env GH_FAIL_SUBJECTS=lintro-linux-arm64
	assert_failure
	[[ ! -e "${OUT}/SHA256SUMS" ]]
	[[ ! -e "${OUT}/lintro.1" ]]
}

@test "a dist SHA256SUMS mismatch fails before any gh call" {
	printf 'tampered\n' >>"${GATE}/dist/lintro-1.2.3.tar.gz"
	run run_gate env
	assert_failure
	assert_output --partial "SHA256SUMS check failed"
	run cat "${GH_LOG}"
	assert_output ""
}

@test "a missing binary fails before any gh call" {
	rm "${GATE}/binaries/lintro-linux-x64"
	run run_gate env
	assert_failure
	assert_output --partial "missing or empty binary"
	run cat "${GH_LOG}"
	assert_output ""
}

@test "a dist without a wheel fails" {
	rm "${GATE}/dist/lintro-1.2.3-py3-none-any.whl"
	run run_gate env
	assert_failure
	assert_output --partial "sdist and one wheel"
}

@test "refuses to assemble into an existing OUT_DIR" {
	mkdir -p "${OUT}"
	run run_gate env
	assert_failure
	assert_output --partial "already exists"
}

@test "requires the signer workflows" {
	run run_gate env BINARY_SIGNER_WORKFLOW=
	assert_failure
	assert_output --partial "BINARY_SIGNER_WORKFLOW is required"
}
