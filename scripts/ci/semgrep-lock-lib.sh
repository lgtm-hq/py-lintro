#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Shared plumbing for the isolated semgrep lockfile (#2436).
#
# Sourced by scripts/ci/compile-semgrep-lock.sh (writes the lockfile) and
# scripts/ci/check-semgrep-lock.sh (CI drift gate). Both go through
# semgrep_lock_compile so the resolver invocation cannot diverge: a flag added
# for one is automatically used by the other, and the gate can never disagree
# with the command it tells contributors to run.
#
# Executing this file directly only prints this help; it defines functions.

set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	cat <<'EOF'
Shared helpers for the isolated semgrep lockfile scripts.

This file is a library: source it, do not run it.

  source scripts/ci/semgrep-lock-lib.sh

Provides:
  semgrep_lock_project_root   Echo the repository root.
  semgrep_lock_require_inputs Fail unless requirements-semgrep.in and uv exist.
  semgrep_lock_compile <out>  Resolve requirements-semgrep.in into <out>
                              (hash-pinned, Python 3.11 floor).
  semgrep_lock_strip_header <file>
                              Echo <file> without uv's generated header
                              comments, which name the output path and so
                              differ between the committed lockfile and a
                              freshly resolved temporary copy.

Entry points:
  scripts/ci/compile-semgrep-lock.sh  Rewrite requirements-semgrep.txt.
  scripts/ci/check-semgrep-lock.sh    Fail when the committed lockfile drifted.
EOF
	exit 0
fi

# Echo the repository root, derived from this file's location.
semgrep_lock_project_root() {
	local lib_dir
	lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	(cd "${lib_dir}/../.." && pwd)
}

# Fail unless the .in pin and the uv resolver are both available.
semgrep_lock_require_inputs() {
	local project_root
	project_root="$(semgrep_lock_project_root)"
	if [[ ! -f "${project_root}/requirements-semgrep.in" ]]; then
		echo "Error: ${project_root}/requirements-semgrep.in not found" >&2
		return 1
	fi
	if ! command -v uv >/dev/null 2>&1; then
		echo "Error: uv is required to compile the isolated semgrep lockfile" >&2
		return 1
	fi
}

# Resolve requirements-semgrep.in into the given output file.
#
# Arguments:
#   $1 - Output path (absolute, or relative to the repository root).
semgrep_lock_compile() {
	local output_file="$1"
	local project_root
	project_root="$(semgrep_lock_project_root)"
	(
		cd "${project_root}"
		uv pip compile \
			--no-config \
			--generate-hashes \
			--python-version 3.11 \
			--output-file "${output_file}" \
			requirements-semgrep.in
	)
}

# Echo a lockfile without uv's generated header.
#
# uv stamps the resolving command — including --output-file — into leading
# `#` comments at column 0. Every comment uv emits for a requirement (`# via
# ...`) is indented, so dropping column-0 comments removes exactly the header
# and leaves the resolved content to compare.
semgrep_lock_strip_header() {
	grep -v '^#' "$1" || true
}
