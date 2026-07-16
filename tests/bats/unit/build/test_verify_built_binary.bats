#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/build/verify_built_binary.sh

load "../../helpers/common"

SCRIPT="${BUILD_SCRIPTS_DIR}/verify_built_binary.sh"

setup() {
	setup_temp_dir
	WORKDIR="${BATS_TEST_TMPDIR}/work"
	mkdir -p "${WORKDIR}/dist/nuitka"
	BINARY="${WORKDIR}/dist/nuitka/lintro"
}

teardown() {
	teardown_temp_dir
}

@test "verify_built_binary.sh: --help exits 0" {
	run bash "$SCRIPT" --help
	assert_success
	assert_output --partial "Verify a built lintro binary"
}

@test "verify_built_binary.sh: missing args exits 2" {
	run bash "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
}

@test "verify_built_binary.sh: fails when binary is missing" {
	run bash "$SCRIPT" "${WORKDIR}/missing"
	assert_failure
	assert_output --partial "Binary not found"
}

@test "verify_built_binary.sh: runs --version on a shell script binary" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
--help) echo "help"; exit 0 ;;
*) exit 1 ;;
esac
EOF
	chmod +x "$BINARY"

	run bash "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "lintro test 0.0.0"
}
