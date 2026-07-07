#!/usr/bin/env bash
set -uo pipefail

# verify-tools.sh
#
# Single source of truth for verifying that every bundled lintro toolchain is
# installed and runnable (issue #822, item 2). Replaces the duplicated blocks of
# ~30 `tool --version` lines that were maintained in the Dockerfile twice.
#
# Runs each tool's version command, prints a pass/fail line per tool, and exits
# non-zero if any check fails so a broken image fails the build immediately.
#
# Usage:
#   scripts/ci/verify-tools.sh [--label <context>]
#
# Options:
#   --label <context>   Context label for logging (e.g. root, non-root, tools).
#   --help, -h          Show this help.

label="default"

while [[ $# -gt 0 ]]; do
	case "$1" in
	--help | -h)
		cat <<'EOF'
Verify all bundled lintro tools are installed and runnable.

Usage:
  scripts/ci/verify-tools.sh [--label <context>]

Options:
  --label <context>   Context label for logging (e.g. root, non-root).
  --help, -h          Show this help.
EOF
		exit 0
		;;
	--label)
		label="${2:-}"
		if [[ -z "$label" ]]; then
			echo "--label requires a value" >&2
			exit 2
		fi
		shift 2
		;;
	--label=*)
		label="${1#*=}"
		shift
		;;
	*)
		echo "Unknown argument: $1" >&2
		exit 2
		;;
	esac
done

# Tool version checks — maintained in ONE place. Each entry is the exact
# command used to prove the tool is installed and runnable.
checks=(
	"bun --version"
	"uv --version"
	"cargo --version"
	"rustc --version"
	"rustfmt --version"
	"cargo clippy --version"
	"cargo audit --version"
	"cargo deny --version"
	"actionlint --version"
	"bandit --version"
	"black --version"
	"gitleaks version"
	"hadolint --version"
	"markdownlint-cli2 --version"
	"mypy --version"
	"osv-scanner --version"
	"oxfmt --version"
	"oxlint --version"
	"prettier --version"
	"pydoclint --version"
	"ruff --version"
	"semgrep --version"
	"shellcheck --version"
	"shfmt --version"
	"sqlfluff --version"
	"taplo --version"
	"tsc --version"
	"astro --version"
	"svelte-check --version"
	"vue-tsc --version"
	"yamllint --version"
)

echo "=== Verifying ${#checks[@]} tools (context: ${label}) ==="

failed=()
for check in "${checks[@]}"; do
	if $check >/dev/null 2>&1; then
		echo "  PASS  ${check}"
	else
		echo "  FAIL  ${check}"
		failed+=("$check")
	fi
done

if [[ ${#failed[@]} -gt 0 ]]; then
	echo "=== ${#failed[@]} tool(s) FAILED (context: ${label}) ===" >&2
	for check in "${failed[@]}"; do
		echo "  - ${check}" >&2
	done
	exit 1
fi

echo "=== All ${#checks[@]} tools verified (context: ${label}) ==="
