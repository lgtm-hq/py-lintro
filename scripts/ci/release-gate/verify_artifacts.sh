#!/usr/bin/env bash
set -euo pipefail

# verify_artifacts.sh
#
# The release gate (#2562): prove every artifact the build stage produced
# before the first irreversible write, then assemble the exact bytes the
# GitHub Release attaches. Fails closed at every step.
#
#   1. The dist directory carries SHA256SUMS, at least one sdist and one
#      wheel, and `sha256sum -c` passes.
#   2. Every expected binary and the man page exist and are non-empty.
#   3. `gh attestation verify` passes for every dist file (signed by lgtm-ci's
#      build reusable) and every binary (signed by build-binaries.yml). A
#      reusable signs as the called file, never as the entry workflow.
#   4. `gh attestation download` fetches each subject's Sigstore bundle,
#      renamed to <asset>.intoto.jsonl so Scorecard's Signed-Releases sees a
#      signature file next to every asset.
#   5. Dist files, binaries, the man page and the bundles are copied into
#      OUT_DIR and a SHA256SUMS over all of them is written there; the
#      release reusable attaches that manifest as is.

show_help() {
	cat <<'EOF'
Verify the build stage's artifacts and assemble the release assets.

Usage:
  GH_TOKEN=<token> ATTESTATION_REPO=<owner/repo> \
    DIST_SIGNER_WORKFLOW=<owner/repo/.github/workflows/x.yml> \
    BINARY_SIGNER_WORKFLOW=<owner/repo/.github/workflows/y.yml> \
    scripts/ci/release-gate/verify_artifacts.sh

Environment:
  GATE_DIR                Directory holding dist/, binaries/ and man/ as
                          downloaded from the run artifacts (default: gate)
  OUT_DIR                 Directory the release assets are assembled into
                          (default: release-assets); must not exist yet
  ATTESTATION_REPO        Repository whose attestations are consulted,
                          passed as `--repo` (required)
  DIST_SIGNER_WORKFLOW    Signer workflow the dist attestations must carry,
                          passed as `--signer-workflow` (required)
  BINARY_SIGNER_WORKFLOW  Signer workflow the binary attestations must
                          carry (required)
  BINARIES                Space-separated binary asset names (default: the
                          three release binaries)
  MAN_PAGE                Man page asset name (default: lintro.1)
  GITHUB_STEP_SUMMARY     When set, one line per verified asset is appended
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

gate_dir="${GATE_DIR:-gate}"
out_dir="${OUT_DIR:-release-assets}"
attestation_repo="${ATTESTATION_REPO:-}"
dist_signer="${DIST_SIGNER_WORKFLOW:-}"
binary_signer="${BINARY_SIGNER_WORKFLOW:-}"
binaries="${BINARIES:-lintro-macos-arm64 lintro-linux-x64 lintro-linux-arm64}"
man_page="${MAN_PAGE:-lintro.1}"

die() {
	echo "::error::$*" >&2
	exit 1
}

for var in ATTESTATION_REPO DIST_SIGNER_WORKFLOW BINARY_SIGNER_WORKFLOW; do
	if [[ -z "${!var:-}" ]]; then
		echo "${var} is required" >&2
		exit 2
	fi
done
if ! command -v gh >/dev/null 2>&1; then
	echo "gh not found; the GitHub CLI is required for attestation verification" >&2
	exit 2
fi
if command -v sha256sum >/dev/null 2>&1; then
	sha_cmd=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
	sha_cmd=(shasum -a 256)
else
	echo "neither sha256sum nor shasum is available" >&2
	exit 2
fi
if [[ -e "$out_dir" ]]; then
	die "OUT_DIR ${out_dir} already exists; refusing to assemble into it"
fi

summary() {
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		printf '%s\n' "$1" >>"$GITHUB_STEP_SUMMARY"
	fi
}

# --- 1. dist -----------------------------------------------------------------
dist_dir="${gate_dir}/dist"
[[ -d "$dist_dir" ]] || die "missing dist directory ${dist_dir}"
[[ -s "${dist_dir}/SHA256SUMS" ]] || die "missing ${dist_dir}/SHA256SUMS"
dist_files=()
while IFS= read -r f; do
	dist_files+=("$f")
done < <(find "$dist_dir" -maxdepth 1 -type f \( -name '*.whl' -o -name '*.tar.gz' \) | sort)
[[ ${#dist_files[@]} -ge 2 ]] || die "expected at least one sdist and one wheel in ${dist_dir}"
has_sdist=false
has_wheel=false
for f in "${dist_files[@]}"; do
	[[ "$f" == *.tar.gz ]] && has_sdist=true
	[[ "$f" == *.whl ]] && has_wheel=true
done
[[ "$has_sdist" == true && "$has_wheel" == true ]] || die "dist must contain both an sdist and a wheel"
echo "Checking ${dist_dir}/SHA256SUMS"
(cd "$dist_dir" && "${sha_cmd[@]}" -c --strict SHA256SUMS) || die "dist SHA256SUMS check failed"

# --- 2. binaries and man page ------------------------------------------------
bin_dir="${gate_dir}/binaries"
binary_files=()
for name in $binaries; do
	[[ -s "${bin_dir}/${name}" ]] || die "missing or empty binary ${bin_dir}/${name}"
	binary_files+=("${bin_dir}/${name}")
done
man_file="${gate_dir}/man/${man_page}"
[[ -s "$man_file" ]] || die "missing or empty man page ${man_file}"

# --- 3 + 4. attestations and bundles -----------------------------------------
mkdir -p "$out_dir"
bundle_tmp="$(mktemp -d)"
trap 'rm -rf "$bundle_tmp"' EXIT

verify_and_bundle() {
	local file="$1" signer="$2" name
	name="$(basename "$file")"
	echo "Verifying attestation for ${name} (signer ${signer})"
	gh attestation verify "$file" \
		--repo "$attestation_repo" \
		--signer-workflow "$signer"
	local abs_file
	abs_file="$(cd "$(dirname "$file")" && pwd)/${name}"
	rm -f "${bundle_tmp}"/*.jsonl
	(cd "$bundle_tmp" && gh attestation download "$abs_file" --repo "$attestation_repo")
	local bundles=("${bundle_tmp}"/*.jsonl)
	if [[ ${#bundles[@]} -ne 1 || ! -s "${bundles[0]}" ]]; then
		die "expected exactly one non-empty bundle for ${name}, found ${#bundles[@]}"
	fi
	cp "${bundles[0]}" "${out_dir}/${name}.intoto.jsonl"
	cp "$file" "${out_dir}/${name}"
	summary "- attestation verified: \`${name}\` (signer \`${signer}\`)"
}

for f in "${dist_files[@]}"; do
	verify_and_bundle "$f" "$dist_signer"
done
for f in "${binary_files[@]}"; do
	verify_and_bundle "$f" "$binary_signer"
done
cp "$man_file" "${out_dir}/${man_page}"
summary "- attached without attestation (generated text): \`${man_page}\`"

# --- 5. SHA256SUMS over every asset ------------------------------------------
(
	cd "$out_dir"
	find . -maxdepth 1 -type f ! -name SHA256SUMS | sed 's|^\./||' | sort |
		xargs "${sha_cmd[@]}"
) >"${out_dir}/SHA256SUMS"
[[ -s "${out_dir}/SHA256SUMS" ]] || die "failed to write ${out_dir}/SHA256SUMS"
(cd "$out_dir" && "${sha_cmd[@]}" -c --strict SHA256SUMS >/dev/null) || die "self-check of ${out_dir}/SHA256SUMS failed"

count=$((${#dist_files[@]} + ${#binary_files[@]}))
echo "Verified ${count} attested asset(s); assembled $(find "$out_dir" -maxdepth 1 -type f | wc -l | tr -d ' ') file(s) in ${out_dir}"
