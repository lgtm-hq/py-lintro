#!/usr/bin/env bash
set -euo pipefail

# restore-codex-session.sh - restore the Codex ChatGPT-plan session on a runner
#
# The openai dogfood lane (#2472) authenticates the `codex` binary through its
# ChatGPT-plan session: the ~/.codex/auth.json file written by `codex login`.
# Codex has no OAuth-token env var (unlike CLAUDE_CODE_OAUTH_TOKEN for the
# claude CLI), and CODEX_API_KEY is not an alternative here — it would bill
# metered API credits instead of the subscription. The credential therefore
# travels as the CODEX_AUTH_JSON secret: the base64 of a locally logged-in
# auth.json, decoded into place by this script.
#
# The caller must inject the secret AFTER the trusted base-ref checkout and
# the pinned CLI installs (the workflow's activation precondition, audited in
# tests/scripts/test_run_ai_review.py). An unset secret is a warning here, not
# a skip: the wrapper's credential gate reports it through the classifier so
# the failure stays visible and classified.
#
# Usage:
#   CODEX_AUTH_JSON=<base64> scripts/ci/restore-codex-session.sh
#
# Environment:
#   CODEX_AUTH_JSON  base64 of ~/.codex/auth.json from a local `codex login`
#                    (required; unset => warning + exit 0, wrapper reports)
#   HOME             target home; the session lands at $HOME/.codex/auth.json

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: CODEX_AUTH_JSON=<base64> scripts/ci/restore-codex-session.sh

Decode the base64 Codex subscription session into $HOME/.codex/auth.json.

Environment:
  CODEX_AUTH_JSON  base64 of auth.json (required; unset => warning + exit 0)
  HOME             session is written to $HOME/.codex/auth.json
EOF
	exit 0
fi

if [[ -z "${CODEX_AUTH_JSON:-}" ]]; then
	echo "::warning::CODEX_AUTH_JSON is unset — review will report it."
	exit 0
fi

mkdir -p "${HOME:-}/.codex"
# A mistyped secret (raw JSON pasted unencoded, a truncated value, invalid
# base64) should fail here with a clear message rather than at the review, as
# a confusing CLI auth error — and must not leave a partial file behind.
auth_target="${HOME:-}/.codex/auth.json"
if ! printf '%s' "${CODEX_AUTH_JSON}" | base64 --decode >"${auth_target}" 2>/dev/null; then
	rm -f "${auth_target}"
	echo "ERROR: CODEX_AUTH_JSON did not decode to a JSON session file" >&2
	exit 1
fi
chmod 600 "${auth_target}"

# auth.json is a JSON object; anything else decoded cleanly but is wrong.
if [[ ! -s "${auth_target}" ]] || [[ "$(head -c 1 "${auth_target}")" != "{" ]]; then
	rm -f "${auth_target}"
	echo "ERROR: CODEX_AUTH_JSON did not decode to a JSON session file" >&2
	exit 1
fi

echo "Codex session restored to ${auth_target}."
