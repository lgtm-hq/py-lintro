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

The flattened list is always written to stdout. Entries are validated as single
host:port values (wildcard hosts allowed); a malformed or empty allowlist fails
the script instead of reaching harden-runner.
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
# Every remaining line must be exactly one `host:port` value: harden-runner
# would otherwise accept a typo'd entry silently and block that host at build
# time, which only surfaces during a release publish.
endpoints="$(
	awk '
		{
			line = $0
			sub(/#.*/, "", line)
			gsub(/^[ \t]+|[ \t]+$/, "", line)
		}
		line == "" { next }
		{
			if (split(line, parts, /[ \t]+/) != 1) {
				printf "ERROR: %s:%d: expected one host:port value, got %s\n", \
					FILENAME, FNR, line > "/dev/stderr"
				invalid = 1
				next
			}
			if (line !~ /^[A-Za-z0-9*][A-Za-z0-9.*_-]*:[0-9]+$/) {
				printf "ERROR: %s:%d: not a host:port value: %s\n", \
					FILENAME, FNR, line > "/dev/stderr"
				invalid = 1
				next
			}
			split(line, host_port, ":")
			port = host_port[2] + 0
			if (port < 1 || port > 65535) {
				printf "ERROR: %s:%d: port out of range: %s\n", \
					FILENAME, FNR, line > "/dev/stderr"
				invalid = 1
				next
			}
			resolved = resolved (resolved == "" ? "" : " ") line
		}
		END {
			if (invalid) {
				exit 1
			}
			print resolved
		}
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
