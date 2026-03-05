#!/usr/bin/env bash
# generate-pypi-formula.sh
# Generate Homebrew formula for lintro from PyPI

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Generate Homebrew formula for lintro from PyPI.

Usage: generate-pypi-formula.sh <version> <output-file>

Arguments:
  version      Package version (e.g., 1.0.0)
  output-file  Path to write the formula (e.g., Formula/lintro.rb)

Requirements:
  - Python 3.11+ with pip and venv

This script uses generate_resources.py with importlib.metadata
to generate dependency resource stanzas (replacing homebrew-pypi-poet).

Examples:
  generate-pypi-formula.sh 1.0.0 Formula/lintro.rb
EOF
	exit 0
fi

VERSION="${1:?Version is required}"
OUTPUT_FILE="${2:?Output file is required}"

# Packages that require special handling (can't build from source in Homebrew)
WHEEL_PACKAGES=("pydoclint" "pydantic_core")

# Packages available as Homebrew formulae (use depends_on instead of bundling)
HOMEBREW_PACKAGES=("bandit" "black" "mypy" "ruff" "yamllint")

log_info "Generating lintro formula for version ${VERSION}"

# Fetch package info using Python helper (outputs URL on line 1, SHA on line 2)
log_info "Fetching package info from PyPI..."
{
	read -r TARBALL_URL
	read -r TARBALL_SHA
} < <(python3 "$SCRIPT_DIR/fetch_package_info.py" lintro "$VERSION")

if [[ -z "$TARBALL_URL" ]] || [[ -z "$TARBALL_SHA" ]]; then
	log_error "Failed to fetch tarball info from PyPI"
	exit 1
fi

log_info "Tarball URL: ${TARBALL_URL}"
log_info "Tarball SHA256: ${TARBALL_SHA}"

# Create temporary directories (cleaned up on exit)
ANALYSIS_VENV=$(mktemp -d)
TMPDIR=$(mktemp -d)
trap 'rm -rf "$ANALYSIS_VENV" "$TMPDIR"' EXIT

log_info "Creating temporary venv for dependency analysis..."
python3 -m venv "$ANALYSIS_VENV"

log_info "Downloading and verifying tarball..."
TARBALL_FILE="$TMPDIR/lintro-${VERSION}.tar.gz"
if ! curl -sSfL "$TARBALL_URL" -o "$TARBALL_FILE"; then
	log_error "Failed to download tarball from $TARBALL_URL"
	exit 1
fi

# Verify hash (use sha256sum on Linux, shasum on macOS)
if command -v sha256sum &>/dev/null; then
	ACTUAL_SHA=$(sha256sum "$TARBALL_FILE" | cut -d' ' -f1)
else
	ACTUAL_SHA=$(shasum -a 256 "$TARBALL_FILE" | cut -d' ' -f1)
fi
if [[ "$ACTUAL_SHA" != "$TARBALL_SHA" ]]; then
	log_error "SHA256 mismatch! Expected: $TARBALL_SHA, Got: $ACTUAL_SHA"
	exit 1
fi
log_info "SHA256 verified"

log_info "Installing lintro from tarball (bypasses pip index propagation delay)..."
"$ANALYSIS_VENV/bin/pip" install --quiet "$TARBALL_FILE"

# Build exclusion list for packages we handle specially
EXCLUDE_ARGS=()
for pkg in "${WHEEL_PACKAGES[@]}" "${HOMEBREW_PACKAGES[@]}"; do
	EXCLUDE_ARGS+=("$pkg")
done

log_info "Generating resources with generate_resources.py (importlib.metadata)..."
RESOURCES=$("$ANALYSIS_VENV/bin/python" "$SCRIPT_DIR/generate_resources.py" lintro \
	--exclude "${EXCLUDE_ARGS[@]}")

# Validate resources were generated
RESOURCE_COUNT=$(echo "$RESOURCES" | grep -c "^  resource " || echo "0")
if [[ "$RESOURCE_COUNT" -lt 5 ]]; then
	log_error "Expected multiple resource stanzas but only found ${RESOURCE_COUNT}"
	log_error "generate_resources.py may have failed to analyze dependencies."
	exit 1
fi
log_info "Generated ${RESOURCE_COUNT} resource stanzas"

# Write resources to temp files
echo "$RESOURCES" >"$TMPDIR/resources.txt"

# Generate wheel resources for packages that can't build from source
log_info "Generating wheel resources for special packages..."

python3 "$SCRIPT_DIR/fetch_wheel_info.py" pydoclint \
	--type universal \
	--comment "pydoclint - use wheel for consistency" \
	>"$TMPDIR/pydoclint.txt" || {
	log_error "Failed to fetch pydoclint wheel info"
	exit 1
}

# Query the installed pydantic-core version from the analysis venv
# (must match the version required by pydantic, not the latest on PyPI)
PYDANTIC_CORE_VERSION=$("$ANALYSIS_VENV/bin/python" -c \
	"
try:
    from importlib.metadata import version
    print(version('pydantic-core'))
except Exception as e:
    import sys
    print(f'Failed to resolve pydantic-core version: {e}', file=sys.stderr)
    sys.exit(1)
") || {
	log_error "Could not determine installed pydantic-core version"
	exit 1
}
log_info "Installed pydantic-core version: ${PYDANTIC_CORE_VERSION}"

python3 "$SCRIPT_DIR/fetch_wheel_info.py" pydantic_core \
	--type platform \
	--version "$PYDANTIC_CORE_VERSION" \
	--comment "pydantic_core requires Rust to build - use platform-specific wheels" \
	>"$TMPDIR/pydantic.txt" || {
	log_error "Failed to fetch pydantic_core wheel info"
	exit 1
}

# Render formula from template
log_info "Rendering formula template..."
python3 "$SCRIPT_DIR/render_formula.py" \
	--tarball-url "$TARBALL_URL" \
	--tarball-sha "$TARBALL_SHA" \
	--poet-resources "$TMPDIR/resources.txt" \
	--pydoclint-resource "$TMPDIR/pydoclint.txt" \
	--pydantic-resource "$TMPDIR/pydantic.txt" \
	--output "$OUTPUT_FILE"

log_success "Formula written to ${OUTPUT_FILE}"
