#!/usr/bin/env bash
# Run pytest for site-related maintenance scripts.
# SPDX-License-Identifier: MIT
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Run pytest for tests/scripts/ci."
	exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

cd "${ROOT}"

if command -v uv >/dev/null 2>&1; then
	if [[ -n "${CI:-}" || -n "${GITHUB_ACTIONS:-}" ]]; then
		uv sync --group test --frozen
	else
		uv sync --group test --frozen 2>/dev/null || uv sync --group test
	fi
	uv run --group test pytest tests/scripts/ci -q
	exit 0
fi

python3 -m pip install --disable-pip-version-check -q -U "pytest>=8.3"
python3 -m pytest tests/scripts/ci -q
