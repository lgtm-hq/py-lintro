#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Flatten a harden-runner egress allowlist file into a single workflow output.
#
# GitHub Actions rejects YAML anchors, and the `env` context is unavailable in
# job-level `with:` blocks of reusable-workflow calls, so a shared allowlist
# has to reach those callers as a job output (#1821).
#
# Required environment variables:
#   ENDPOINTS_FILE - Path to the allowlist file (one host:port per line;
#                    blank lines and `#` comments are ignored)
#
# Optional environment variables:
#   GITHUB_OUTPUT  - When set, `endpoints=<list>` is appended to it

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Flatten a harden-runner egress allowlist file into a single-line list.

Usage:
  ENDPOINTS_FILE=.github/allowed-endpoints/docker-build-publish.txt \
    scripts/ci/resolve-allowed-endpoints.sh

Environment:
  ENDPOINTS_FILE  Allowlist file; one host:port per line, `#` comments ignored.
  GITHUB_OUTPUT   When set, the flattened list is appended as `endpoints=<list>`.

The flattened list is always written to stdout.
EOF
	exit 0
fi

endpoints_file="${ENDPOINTS_FILE:-}"

if [[ -z "${endpoints_file}" ]]; then
	echo "ERROR: ENDPOINTS_FILE is required" >&2
	exit 1
fi

if [[ ! -f "${endpoints_file}" ]]; then
	echo "ERROR: allowlist file not found: ${endpoints_file}" >&2
	exit 1
fi

# Strip comments and surrounding whitespace, drop blank lines, join with spaces.
endpoints="$(
	awk '
		{ sub(/#.*/, ""); gsub(/^[ \t]+|[ \t]+$/, "") }
		$0 != "" { printf "%s%s", (seen++ ? " " : ""), $0 }
		END { print "" }
	' "${endpoints_file}"
)"

if [[ -z "${endpoints}" ]]; then
	echo "ERROR: no endpoints found in ${endpoints_file}" >&2
	exit 1
fi

# An empty allowlist under `replace` semantics would block all egress, so the
# emptiness check above must stay ahead of any output write.
echo "${endpoints}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	echo "endpoints=${endpoints}" >>"${GITHUB_OUTPUT}"
fi
