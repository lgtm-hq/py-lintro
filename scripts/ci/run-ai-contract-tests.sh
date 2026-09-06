#!/usr/bin/env bash
set -euo pipefail

# run-ai-contract-tests.sh - run a tier of the AI CLI contract suite
#
# lintro's `--transport cli` providers drive three external agent binaries whose
# flag surfaces move independently of lintro. The contract suite is what turns
# that drift into a failed check instead of a broken review (#1611, #1614):
#
#   tier 1  flag surface. Runs `--version` and `--help` only, so it costs no
#           quota and needs no credential. Safe to gate every change on.
#   tier 2  real invocation. Walks presence -> liveness -> invoke per provider and
#           spends quota, so it is scheduled rather than run on every PR.
#
# Both tiers run inside the published lintro-ai-tools image rather than
# installing the CLIs on the runner. That image is the artifact users actually
# get, its CLI versions are digest-pinned and Renovate-managed, and pulling one
# image beats three external downloads whose outages would otherwise redden every
# pull request.
#
# The suite refuses to skip silently: LINTRO_CONTRACT_REQUIRE_BINARIES is set
# here, so a binary that is missing inside the image fails the tier instead of
# quietly reducing it to zero assertions.
#
# Usage:
#   IMAGE=<ref> TIER=1 scripts/ci/run-ai-contract-tests.sh
#   IMAGE=<ref> TIER=2 ANTHROPIC_API_KEY=<key> scripts/ci/run-ai-contract-tests.sh
#
# Environment:
#   IMAGE               Fully qualified lintro-ai-tools reference   (required)
#   TIER                1 or 2                                      (required)
#   ANTHROPIC_API_KEY   Forwarded for tier 2 (optional; absence is a
#                       visible skip, never a silent pass)
#   CODEX_API_KEY       Forwarded for tier 2 (optional)
#   CURSOR_API_KEY      Forwarded for tier 2 (optional)

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: IMAGE=<ref> TIER=<1|2> scripts/ci/run-ai-contract-tests.sh

AI CLI Contract Tests
Runs one tier of the agent-CLI contract suite inside the lintro-ai-tools image.

Tier 1: free flag-surface check (--version/--help only).
Tier 2: real-invocation smoke; spends provider quota.

Environment:
  IMAGE               lintro-ai-tools image reference  (required)
  TIER                1 or 2                           (required)
  ANTHROPIC_API_KEY   Forwarded to tier 2              (optional)
  CODEX_API_KEY       Forwarded to tier 2              (optional)
  CURSOR_API_KEY      Forwarded to tier 2              (optional)
EOF
	exit 0
fi

if [ -z "${IMAGE:-}" ]; then
	echo "ERROR: IMAGE is required" >&2
	exit 1
fi

case "${TIER:-}" in
1 | 2) ;;
*)
	echo "ERROR: TIER must be 1 or 2 (got '${TIER:-}')" >&2
	exit 1
	;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

docker_args=(
	run --rm
	--volume "${repo_root}:/work"
	--workdir /work
	# A missing binary inside this image is a broken gate, not an absent
	# developer tool, so the suite fails instead of skipping.
	--env LINTRO_CONTRACT_REQUIRE_BINARIES=1
	# uv must not try to write to a read-only or absent HOME inside the image.
	--env "UV_CACHE_DIR=/tmp/uv-cache"
	# The synced environment stays off the bind mount: /work is the caller's
	# checkout, so a container-created .venv would overwrite a developer's own
	# and leave root-owned files behind for later CI steps.
	--env "UV_PROJECT_ENVIRONMENT=/tmp/contract-venv"
	# Same reason, for the two caches uv does not control: importing the test
	# modules writes __pycache__ and pytest writes its own cache directory.
	--env "PYTHONPYCACHEPREFIX=/tmp/pycache"
	# The cache and the mounted workspace are on different filesystems, so uv
	# cannot hardlink packages between them.
	--env "UV_LINK_MODE=copy"
	--env "HOME=/tmp"
)

pytest_marker="contract_tier1"
if [ "$TIER" = "2" ]; then
	pytest_marker="contract_tier2"
	docker_args+=(--env LINTRO_CONTRACT_TIER2=1)
	# Forwarded without defaults: an unset credential must reach the suite as
	# unset so it reports a visible skip naming the missing link.
	for secret in ANTHROPIC_API_KEY CODEX_API_KEY CURSOR_API_KEY; do
		if [ -n "${!secret:-}" ]; then
			docker_args+=(--env "${secret}")
		fi
	done
fi

echo "==> Tier ${TIER} contract tests in ${IMAGE}"

# `uv sync` inside the container: pytest and assertpy are dev dependencies and
# the image ships only the lint toolchain. `--locked` because /work is the
# caller's checkout: without it a metadata drift would be resolved by silently
# rewriting `uv.lock` there, where the gate should fail and say so instead.
#
# --maxfail=0 is explicit rather than inherited: drift across three providers
# must be reported in full, whatever a future config change does to the default.
docker "${docker_args[@]}" "$IMAGE" bash -euo pipefail -c "
	uv sync --locked --extra ai --group dev --group ai-runtime --quiet
	uv run --locked pytest tests/contract -m ${pytest_marker} -p no:randomly --maxfail=0 \\
		-o cache_dir=/tmp/pytest-cache
"
