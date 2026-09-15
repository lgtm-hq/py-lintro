#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# release-fault.sh
#
# Fault injection for the release validation runbook (#2633,
# docs/release-validation.md). The tag pipeline's classify-tag job reads the
# RELEASE_FAULT repository variable once and passes it down as an output; a
# fault step calls this script with its own fault name and the output in
# RELEASE_FAULT. The step fails, with a message that names the fault, only
# when the two are equal. Any other value, including unset, is a no-op, so
# the default pipeline behaves exactly as if the step were absent.

show_help() {
	cat <<'EOF'
Fail the current step when RELEASE_FAULT names this fault; otherwise no-op.

Usage:
  RELEASE_FAULT=<name> scripts/ci/release-fault.sh <fault-name>

Arguments:
  fault-name     The fault this step injects: fail-build or fail-publish-npm

Environment:
  RELEASE_FAULT  The fault the run asked for (classify-tag's release_fault
                 output, from the RELEASE_FAULT repository variable). Unset
                 or any value other than <fault-name> injects nothing.

Exit codes:
  0  RELEASE_FAULT does not name this fault (the default)
  1  RELEASE_FAULT equals <fault-name>: the fault fired
  2  Usage error (missing or unknown fault name)

Examples:
  scripts/ci/release-fault.sh fail-build
  RELEASE_FAULT=fail-build scripts/ci/release-fault.sh fail-build   # exits 1
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

if [[ $# -ne 1 ]]; then
	echo "release-fault.sh: exactly one fault name is required" >&2
	show_help >&2
	exit 2
fi

fault_name="$1"
case "$fault_name" in
fail-build | fail-publish-npm) ;;
*)
	echo "release-fault.sh: unknown fault name: ${fault_name}" >&2
	exit 2
	;;
esac

requested="${RELEASE_FAULT:-}"

if [[ "$requested" == "$fault_name" ]]; then
	echo "::error title=Injected release fault::RELEASE_FAULT=${fault_name}: failing this step on purpose (#2633 validation). Unset the repository variable to stop injecting it."
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		echo "- injected fault \`${fault_name}\` fired (RELEASE_FAULT repository variable)" >>"$GITHUB_STEP_SUMMARY"
	fi
	exit 1
fi

if [[ -n "$requested" ]]; then
	echo "RELEASE_FAULT=${requested} does not name this step (${fault_name}); nothing injected."
else
	echo "RELEASE_FAULT is unset; nothing injected (${fault_name})."
fi
