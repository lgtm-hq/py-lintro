#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Common utilities for py-lintro BATS tests.

HELPERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${HELPERS_DIR}/../../.." && pwd)"
export PROJECT_ROOT
export BUILD_SCRIPTS_DIR="${PROJECT_ROOT}/scripts/build"

_load_bats_library() {
	local name="$1"
	local paths=(
		"${BATS_TEST_DIRNAME}/../../node_modules/bats-${name}/load.bash"
		"/opt/homebrew/lib/bats-${name}/load.bash"
		"/usr/local/lib/bats-${name}/load.bash"
		"/usr/lib/bats-${name}/load.bash"
	)

	for path in "${paths[@]}"; do
		if [[ -f "${path}" ]]; then
			# shellcheck disable=SC1090
			source "${path}"
			return 0
		fi
	done

	return 1
}

if ! _load_bats_library "support"; then
	fail() {
		echo "# $*" >&2
		return 1
	}
fi

if ! _load_bats_library "assert"; then
	# shellcheck disable=SC2154
	assert_success() {
		[[ "${status}" -eq 0 ]] || {
			echo "# Expected success, got exit ${status}" >&2
			echo "# Output: ${output}" >&2
			return 1
		}
	}

	# shellcheck disable=SC2154
	assert_failure() {
		[[ "${status}" -ne 0 ]] || {
			echo "# Expected failure, got exit 0" >&2
			echo "# Output: ${output}" >&2
			return 1
		}
	}

	# shellcheck disable=SC2154
	assert_output() {
		local expected
		if [[ "$1" == "--partial" ]]; then
			expected="$2"
			[[ "${output}" == *"${expected}"* ]] || {
				echo "# Expected output to contain: ${expected}" >&2
				echo "# Actual output: ${output}" >&2
				return 1
			}
		else
			expected="$1"
			[[ "${output}" == "${expected}" ]] || {
				echo "# Expected output: ${expected}" >&2
				echo "# Actual output: ${output}" >&2
				return 1
			}
		fi
	}

	assert_equal() {
		[[ "$1" == "$2" ]] || {
			echo "# Expected: $1" >&2
			echo "# Actual:   $2" >&2
			return 1
		}
	}
fi

setup_temp_dir() {
	if [[ -z "${BATS_TEST_TMPDIR:-}" ]]; then
		local tmpdir
		if ! tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/bats-test.XXXXXXXXXX")"; then
			echo "# Failed to create BATS temp directory" >&2
			return 1
		fi
		export BATS_TEST_TMPDIR="${tmpdir}"
	fi
}

teardown_temp_dir() {
	if [[ -n "${BATS_TEST_TMPDIR:-}" ]] && [[ -d "${BATS_TEST_TMPDIR}" ]]; then
		rm -rf "${BATS_TEST_TMPDIR}"
	fi
}

setup_github_env() {
	export GITHUB_OUTPUT="${BATS_TEST_TMPDIR}/github_output"
	touch "$GITHUB_OUTPUT"
}

get_github_output() {
	local key="$1"
	grep -m1 "^${key}=" "${GITHUB_OUTPUT}" | cut -d= -f2-
}

compute_expected_sha256() {
	local file="$1"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$file" | cut -d' ' -f1
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$file" | cut -d' ' -f1
	else
		echo "# No SHA256 tool available" >&2
		return 1
	fi
}

create_fake_binary() {
	local path="$1"
	local content="${2:-fake-binary}"
	mkdir -p "$(dirname "$path")"
	printf '%s\n' "$content" >"$path"
	chmod +x "$path"
}
