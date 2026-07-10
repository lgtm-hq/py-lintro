#!/usr/bin/env bash
# Stage flat HTML coverage for GitHub Pages Model B bundling.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Generate coverage-html/ from the CI python-coverage artifact."
	echo "Prefers coverage.py binary data (.coverage), then a prebuilt htmlcov/"
	echo "tree, then renders a summary index from coverage.json."
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
OUTPUT_DIR="${ROOT}/coverage-html"
REPORT_DIR="${ROOT}/coverage-report"
COVERAGE_DATA="${REPORT_DIR}/.coverage"
COVERAGE_JSON="${REPORT_DIR}/coverage.json"
RENDER_JSON_HTML="${SCRIPT_DIR}/render-coverage-json-html.py"

cd "${ROOT}"

rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"

if [[ -f "${COVERAGE_DATA}" ]]; then
	# coverage html --data-file requires coverage.py's binary data file, not
	# Cobertura XML / coverage.py JSON reports.
	uv run coverage html -d "${OUTPUT_DIR}" --data-file "${COVERAGE_DATA}"
elif [[ -f "${REPORT_DIR}/htmlcov/index.html" ]]; then
	cp -a "${REPORT_DIR}/htmlcov/." "${OUTPUT_DIR}/"
elif [[ -f "${COVERAGE_JSON}" ]]; then
	uv run python "${RENDER_JSON_HTML}" \
		--input "${COVERAGE_JSON}" \
		--output-dir "${OUTPUT_DIR}"
else
	echo "::error::No usable coverage inputs in ${REPORT_DIR}"
	echo "::error::Expected .coverage, htmlcov/index.html, or coverage.json"
	ls -la "${REPORT_DIR}" || true
	exit 1
fi

if [[ ! -f "${OUTPUT_DIR}/index.html" ]]; then
	echo "::error::coverage staging did not produce index.html"
	exit 1
fi

echo "Staged coverage HTML at ${OUTPUT_DIR}"
