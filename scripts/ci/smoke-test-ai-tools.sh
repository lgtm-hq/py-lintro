#!/usr/bin/env bash
set -euo pipefail

# smoke-test-ai-tools.sh - post-build validation for the lintro-ai-tools image
#
# Runs every agent CLI the image exists to provide, on the platform staging
# image, before the multi-arch manifest is merged. The build-time RUN steps
# only ever exercise the architecture they were built on, so this is what
# catches a broken per-platform npm resolution or a corrupt arch-specific
# Cursor tarball -- checking a single CLI would leave two of the three
# transports unvalidated in the published manifest.
#
# Called by .github/workflows/docker-ai-tools-publish.yml through the
# reusable-docker `smoke-test-script` input, which supplies:
#
# Environment:
#   IMAGE      Fully qualified staging image reference (required)
#   PLATFORM   Platform being validated, e.g. linux/arm64 (required)
#   REGISTRY   Registry host (unused here; set by the reusable)

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: smoke-test-ai-tools.sh [--help]

AI Tools Image Smoke Test
Runs every baked agent CLI inside the given platform staging image.

Options:
  --help, -h     Show this help message

Environment:
  IMAGE      Fully qualified staging image reference (required)
  PLATFORM   Platform being validated, e.g. linux/arm64 (required)
EOF
	exit 0
fi

if [ -z "${IMAGE:-}" ]; then
	echo "ERROR: IMAGE is required" >&2
	exit 1
fi
if [ -z "${PLATFORM:-}" ]; then
	echo "ERROR: PLATFORM is required" >&2
	exit 1
fi

# Keep in step with the binaries declared in
# lintro/ai/providers/cli_contracts.py; tests/unit/test_ai_tools_image.py
# fails the build if the two lists drift apart.
BINARIES=(claude codex agent)

for binary in "${BINARIES[@]}"; do
	echo "==> ${PLATFORM}: ${binary} --version"
	docker run --rm --platform "$PLATFORM" "$IMAGE" "$binary" --version
done

echo "==> ${PLATFORM}: all AI agent CLIs responded"
