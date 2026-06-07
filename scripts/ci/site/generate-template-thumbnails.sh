#!/usr/bin/env bash
# Generate PNG thumbnails for resume templates (legacy Rustume helper; optional).
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Generate template PNGs under apps/site/public/assets/templates/."
	echo "Requires rustume-cli in the workspace (optional maintenance script)."
	exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT_DIR="${ROOT}/apps/site/public/assets/templates"
SAMPLE="$(mktemp)"
trap 'rm -f "${SAMPLE}"' EXIT
TEMPLATES=(
	rhyhorn azurill pikachu nosepass bronzor chikorita
	ditto gengar glalie kakuna leafish onyx
)

mkdir -p "${OUT_DIR}"

cd "${ROOT}"
cargo run -p rustume-cli -- init --sample -o "${SAMPLE}" &>/dev/null

for template in "${TEMPLATES[@]}"; do
	cargo run -p rustume-cli -- preview "${SAMPLE}" -t "${template}" \
		-o "${OUT_DIR}/${template}.png" &>/dev/null
	echo "Generated ${template}.png"
done
