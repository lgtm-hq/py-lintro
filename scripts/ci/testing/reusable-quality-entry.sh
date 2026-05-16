#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Run quality gate in reusable workflow: install tools, check, export version.
EOF
	exit 0
fi

./scripts/utils/install-tools.sh --local
echo "$HOME/.local/bin" >>"$GITHUB_PATH"

# Verify generated tool-version artifacts are in sync with package.json/pyproject.toml
echo "Verifying generated tool versions..."
python3 scripts/ci/generate-tool-versions.py --check

uv run lintro check . --output-format grid

python scripts/utils/extract-version.py | tee ver.txt
cat ver.txt >>"$GITHUB_OUTPUT"
