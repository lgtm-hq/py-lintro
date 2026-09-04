#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Explain a red code-quality gate that never got a lint verdict (#2296).
#
# The required lintro-code-quality check fails closed when the dogfooding lint
# job is killed, cancelled, or times out, so the red check must not read like a
# lint violation. This writes a job-summary line naming the real cause and the
# pending auto-rerun.
#
# Optional environment variables:
#   GATE_INFRA_FLAKE  'true' when the gate failure (or absorbed job failure)
#                     was runner noise rather than a lint violation
#   GATE_STATUS       Gate status output (passed, failed, no-verdict)
#   GATE_RESULT       Gate result output (success, failure)
#   MAX_RERUN_ATTEMPTS  Rerun budget in auto-rerun-on-infra-failure.yml
#   GITHUB_RUN_ATTEMPT  Current run attempt (GitHub-provided)
#   GITHUB_STEP_SUMMARY Markdown summary file (GitHub-provided)

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Explain a red code-quality gate that never got a lint verdict (#2296).

Usage:
  GATE_INFRA_FLAKE=true GATE_STATUS=no-verdict \
    scripts/ci/summarize-code-quality-gate.sh

Writes nothing unless GATE_INFRA_FLAKE is exactly 'true'.
EOF
	exit 0
fi

if [[ "${GATE_INFRA_FLAKE:-}" != "true" ]]; then
	exit 0
fi

attempt="${GITHUB_RUN_ATTEMPT:-1}"
max_attempts="${MAX_RERUN_ATTEMPTS:-3}"

if [[ "${GATE_STATUS:-}" == "no-verdict" ]]; then
	summary="🚦 **No lint verdict (runner loss); auto-rerun will retry** "
	summary+="(attempt ${attempt} of ${max_attempts}).\n\n"
	summary+="The dogfooding lint job was killed, cancelled, or timed out "
	summary+="before it reported a verdict, so the required "
	summary+="\`lintro-code-quality\` check fails closed (#2296). This is not "
	summary+="a lint violation: nothing was linted. "
	summary+="\`auto-rerun-on-infra-failure.yml\` reruns the failed jobs, and "
	summary+="the check goes green once a rerun produces a real verdict."
else
	summary="🚦 **Lint passed; a post-lint step of the job failed.**\n\n"
	summary+="The lint verdict is authoritative, so the required check stays "
	summary+="green, but \`infra-flake=true\` means the run is not proof of a "
	summary+="complete pipeline and \`publish\` refuses to promote the image."
fi

printf '%b\n' "${summary}"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
	printf '%b\n' "${summary}" >>"${GITHUB_STEP_SUMMARY}"
fi
