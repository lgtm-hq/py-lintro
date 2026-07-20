#!/usr/bin/env bash
# Prebuilt binary tool installers (curl/tar downloads).
# Sourced by install-tools.sh or runnable directly.
set -euo pipefail

_BINARY_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_helpers.sh disable=SC1091
source "$_BINARY_TOOLS_DIR/_helpers.sh"

install_hadolint() {
	if ! should_install "hadolint"; then
		log_verbose "Skipping hadolint (not in --tools filter)"
		return 0
	fi
	# Install hadolint (Docker linting)
	# hadolint with checksum verification when available
	local HADOLINT_VERSION
	HADOLINT_VERSION=$(get_tool_version "hadolint") || exit 1
	install_tool_curl "hadolint" \
		"https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint"
}

install_gitleaks() {
	if ! should_install "gitleaks"; then
		log_verbose "Skipping gitleaks (not in --tools filter)"
		return 0
	fi
	# Install gitleaks (secret detection)
	echo -e "${BLUE}Installing gitleaks...${NC}"
	local GITLEAKS_VERSION
	GITLEAKS_VERSION=$(get_tool_version "gitleaks") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install gitleaks v${GITLEAKS_VERSION}"
	elif command -v gitleaks &>/dev/null; then
		echo -e "${GREEN}✓ gitleaks already installed${NC}"
	else
		local tmpdir os arch arch_name tgz_url checksum_url expected actual
		tmpdir=$(mktemp -d)
		os=$(uname -s | tr '[:upper:]' '[:lower:]')
		arch=$(uname -m)
		case "$arch" in
		x86_64 | amd64) arch_name="x64" ;;
		aarch64 | arm64) arch_name="arm64" ;;
		*) echo -e "${RED}✗ Unsupported architecture: $arch${NC}" && exit 1 ;;
		esac
		tgz_url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${os}_${arch_name}.tar.gz"
		checksum_url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_checksums.txt"
		if download_with_retries "$tgz_url" "$tmpdir/gitleaks.tar.gz" 3; then
			# Require checksum verification before installing
			if ! download_with_retries "$checksum_url" "$tmpdir/checksums.txt" 3; then
				echo -e "${RED}✗ Failed to download checksum file for gitleaks${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			echo -e "${BLUE}Verifying checksum for gitleaks...${NC}"
			expected=$(grep "gitleaks_${GITLEAKS_VERSION}_${os}_${arch_name}.tar.gz" "$tmpdir/checksums.txt" | awk '{print $1}')
			if [ -z "$expected" ]; then
				echo -e "${RED}✗ Checksum entry not found for gitleaks_${GITLEAKS_VERSION}_${os}_${arch_name}.tar.gz in ${tmpdir}/checksums.txt${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if command -v sha256sum >/dev/null 2>&1; then
				actual=$(sha256sum "$tmpdir/gitleaks.tar.gz" | awk '{print $1}')
			elif command -v shasum >/dev/null 2>&1; then
				actual=$(shasum -a 256 "$tmpdir/gitleaks.tar.gz" | awk '{print $1}')
			else
				echo -e "${RED}✗ Unable to compute checksum: no hash tool found (sha256sum or shasum required)${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if [ "$expected" != "$actual" ]; then
				echo -e "${RED}✗ Checksum mismatch for gitleaks (expected: $expected, got: $actual)${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			echo -e "${GREEN}✓ Checksum verified${NC}"
			tar -xzf "$tmpdir/gitleaks.tar.gz" -C "$tmpdir"
			cp "$tmpdir/gitleaks" "$BIN_DIR/gitleaks"
			chmod +x "$BIN_DIR/gitleaks"
			echo -e "${GREEN}✓ gitleaks installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to download gitleaks${NC}"
			rm -rf "$tmpdir"
			exit 1
		fi
		rm -rf "$tmpdir"
	fi
}

install_osv_scanner() {
	if ! should_install "osv-scanner"; then
		log_verbose "Skipping osv-scanner (not in --tools filter)"
		return 0
	fi
	# Install osv-scanner (multi-ecosystem vulnerability scanner)
	echo -e "${BLUE}Installing osv-scanner...${NC}"
	local OSV_SCANNER_VERSION
	OSV_SCANNER_VERSION=$(get_tool_version "osv_scanner") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install osv-scanner v${OSV_SCANNER_VERSION}"
	elif command -v osv-scanner &>/dev/null; then
		echo -e "${GREEN}✓ osv-scanner already installed${NC}"
	else
		local os arch arch_name binary_url checksum_url tmpdir expected actual
		os=$(uname -s | tr '[:upper:]' '[:lower:]')
		arch=$(uname -m)
		case "$arch" in
		x86_64 | amd64) arch_name="amd64" ;;
		aarch64 | arm64) arch_name="arm64" ;;
		*) echo -e "${RED}✗ Unsupported architecture: $arch${NC}" && exit 1 ;;
		esac
		binary_url="https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_${os}_${arch_name}"
		checksum_url="https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_SHA256SUMS"
		tmpdir=$(mktemp -d)
		if download_with_retries "$binary_url" "$tmpdir/osv-scanner" 3; then
			chmod +x "$tmpdir/osv-scanner"
			# Require checksum verification before installing
			if ! download_with_retries "$checksum_url" "$tmpdir/checksums.txt" 3; then
				echo -e "${RED}✗ Failed to download checksum file for osv-scanner${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			echo -e "${BLUE}Verifying checksum for osv-scanner...${NC}"
			expected=$(grep "osv-scanner_${os}_${arch_name}$" "$tmpdir/checksums.txt" | awk '{print $1}')
			if [ -z "$expected" ]; then
				echo -e "${RED}✗ No checksum entry for osv-scanner_${os}_${arch_name}${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if command -v sha256sum >/dev/null 2>&1; then
				actual=$(sha256sum "$tmpdir/osv-scanner" | awk '{print $1}')
			elif command -v shasum >/dev/null 2>&1; then
				actual=$(shasum -a 256 "$tmpdir/osv-scanner" | awk '{print $1}')
			else
				echo -e "${RED}✗ No sha256sum or shasum available for checksum verification${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if [ "$expected" != "$actual" ]; then
				echo -e "${RED}✗ Checksum mismatch for osv-scanner${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			echo -e "${GREEN}✓ Checksum verified${NC}"
			mv "$tmpdir/osv-scanner" "$BIN_DIR/osv-scanner"
			rm -rf "$tmpdir"
			echo -e "${GREEN}✓ osv-scanner installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to download osv-scanner${NC}"
			rm -rf "$tmpdir"
			exit 1
		fi
	fi
}

install_actionlint() {
	if ! should_install "actionlint"; then
		log_verbose "Skipping actionlint (not in --tools filter)"
		return 0
	fi
	# Install actionlint (GitHub Actions workflow linter)
	# Prebuilt binaries: https://github.com/rhysd/actionlint/releases
	echo -e "${BLUE}Installing actionlint...${NC}"
	local ACTIONLINT_VERSION
	ACTIONLINT_VERSION="v$(get_tool_version "actionlint")" || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install actionlint ${ACTIONLINT_VERSION}"
	else
		local tmpdir os arch os_name arch_name tgz_url tgz_name checksum_url expected actual
		tmpdir=$(mktemp -d)
		os=$(uname -s)
		arch=$(uname -m)
		case "$os" in
		Darwin) os_name="darwin" ;;
		Linux) os_name="linux" ;;
		*) os_name="linux" ;;
		esac
		case "$arch" in
		x86_64 | amd64) arch_name="amd64" ;;
		aarch64 | arm64) arch_name="arm64" ;;
		*) echo -e "${RED}✗ Unsupported architecture: $arch${NC}" && exit 1 ;;
		esac
		tgz_url="https://github.com/rhysd/actionlint/releases/download/${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION#v}_${os_name}_${arch_name}.tar.gz"
		tgz_name="actionlint_${ACTIONLINT_VERSION#v}_${os_name}_${arch_name}.tar.gz"
		if download_with_retries "$tgz_url" "$tmpdir/actionlint.tgz" 3; then
			# Require checksum verification before installing
			checksum_url="https://github.com/rhysd/actionlint/releases/download/${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION#v}_checksums.txt"
			if ! download_with_retries "$checksum_url" "$tmpdir/checksums.txt" 3; then
				echo -e "${RED}✗ Failed to download checksum file for actionlint${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			echo -e "${BLUE}Verifying checksum for actionlint...${NC}"
			expected=$(grep "$tgz_name" "$tmpdir/checksums.txt" | awk '{print $1}')
			if [ -z "$expected" ]; then
				echo -e "${RED}✗ Checksum entry not found for ${tgz_name}${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if command -v sha256sum >/dev/null 2>&1; then
				actual=$(sha256sum "$tmpdir/actionlint.tgz" | awk '{print $1}')
			elif command -v shasum >/dev/null 2>&1; then
				actual=$(shasum -a 256 "$tmpdir/actionlint.tgz" | awk '{print $1}')
			else
				echo -e "${RED}✗ No hash tool found (sha256sum or shasum required)${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if [ "$expected" != "$actual" ]; then
				echo -e "${RED}✗ Checksum mismatch for actionlint (expected: $expected, got: $actual)${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			echo -e "${GREEN}✓ Checksum verified${NC}"
			if ! tar -xzf "$tmpdir/actionlint.tgz" -C "$tmpdir" >/dev/null 2>&1; then
				echo -e "${RED}✗ Failed to extract actionlint archive${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if [ -f "$tmpdir/actionlint" ]; then
				cp "$tmpdir/actionlint" "$BIN_DIR/actionlint"
				chmod +x "$BIN_DIR/actionlint"
				echo -e "${GREEN}✓ actionlint installed successfully${NC}"
			else
				echo -e "${RED}✗ Could not find extracted actionlint binary${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
		else
			echo -e "${RED}✗ Failed to download actionlint prebuilt binary${NC}"
			rm -rf "$tmpdir"
			exit 1
		fi
		rm -rf "$tmpdir" || true
	fi
}

install_shfmt() {
	if ! should_install "shfmt"; then
		log_verbose "Skipping shfmt (not in --tools filter)"
		return 0
	fi
	# Install shfmt (shell script formatter)
	echo -e "${BLUE}Installing shfmt...${NC}"
	local SHFMT_VERSION
	SHFMT_VERSION=$(get_tool_version "shfmt") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install shfmt v${SHFMT_VERSION}"
	elif command -v shfmt &>/dev/null; then
		echo -e "${GREEN}✓ shfmt already installed${NC}"
	else
		local os arch binary_url
		os=$(uname -s | tr '[:upper:]' '[:lower:]')
		arch=$(uname -m)
		case "$arch" in
		x86_64 | amd64) arch="amd64" ;;
		aarch64 | arm64) arch="arm64" ;;
		esac
		binary_url="https://github.com/mvdan/sh/releases/download/v${SHFMT_VERSION}/shfmt_v${SHFMT_VERSION}_${os}_${arch}"
		if download_with_retries "$binary_url" "$BIN_DIR/shfmt" 3; then
			chmod +x "$BIN_DIR/shfmt"
			echo -e "${GREEN}✓ shfmt installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to download shfmt${NC}"
			exit 1
		fi
	fi
}

install_vale() {
	if ! should_install "vale"; then
		log_verbose "Skipping vale (not in --tools filter)"
		return 0
	fi
	# Install vale (prose/documentation linter)
	# Prebuilt binaries: https://github.com/errata-ai/vale/releases
	echo -e "${BLUE}Installing vale...${NC}"
	local VALE_VERSION
	VALE_VERSION=$(get_tool_version "vale") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install vale v${VALE_VERSION}"
	elif command -v vale &>/dev/null; then
		echo -e "${GREEN}✓ vale already installed${NC}"
	else
		local tmpdir os arch os_name arch_name tgz_name tgz_url checksum_url expected actual
		tmpdir=$(mktemp -d)
		os=$(uname -s)
		arch=$(uname -m)
		case "$os" in
		Darwin) os_name="macOS" ;;
		Linux) os_name="Linux" ;;
		*) os_name="Linux" ;;
		esac
		case "$arch" in
		x86_64 | amd64) arch_name="64-bit" ;;
		aarch64 | arm64) arch_name="arm64" ;;
		*) echo -e "${RED}✗ Unsupported architecture: $arch${NC}" && rm -rf "$tmpdir" && exit 1 ;;
		esac
		tgz_name="vale_${VALE_VERSION}_${os_name}_${arch_name}.tar.gz"
		tgz_url="https://github.com/errata-ai/vale/releases/download/v${VALE_VERSION}/${tgz_name}"
		if download_with_retries "$tgz_url" "$tmpdir/vale.tgz" 3; then
			# Require checksum verification before installing
			checksum_url="https://github.com/errata-ai/vale/releases/download/v${VALE_VERSION}/vale_${VALE_VERSION}_checksums.txt"
			if ! download_with_retries "$checksum_url" "$tmpdir/checksums.txt" 3; then
				echo -e "${RED}✗ Failed to download checksum file for vale${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			echo -e "${BLUE}Verifying checksum for vale...${NC}"
			expected=$(grep "$tgz_name" "$tmpdir/checksums.txt" | awk '{print $1}')
			if [ -z "$expected" ]; then
				echo -e "${RED}✗ Checksum entry not found for ${tgz_name}${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if command -v sha256sum >/dev/null 2>&1; then
				actual=$(sha256sum "$tmpdir/vale.tgz" | awk '{print $1}')
			elif command -v shasum >/dev/null 2>&1; then
				actual=$(shasum -a 256 "$tmpdir/vale.tgz" | awk '{print $1}')
			else
				echo -e "${RED}✗ No hash tool found (sha256sum or shasum required)${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if [ "$expected" != "$actual" ]; then
				echo -e "${RED}✗ Checksum mismatch for vale (expected: $expected, got: $actual)${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			echo -e "${GREEN}✓ Checksum verified${NC}"
			if ! tar -xzf "$tmpdir/vale.tgz" -C "$tmpdir" >/dev/null 2>&1; then
				echo -e "${RED}✗ Failed to extract vale archive${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			if [ -f "$tmpdir/vale" ]; then
				cp "$tmpdir/vale" "$BIN_DIR/vale"
				chmod +x "$BIN_DIR/vale"
				echo -e "${GREEN}✓ vale installed successfully${NC}"
			else
				echo -e "${RED}✗ Could not find extracted vale binary${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
		else
			echo -e "${RED}✗ Failed to download vale prebuilt binary${NC}"
			rm -rf "$tmpdir"
			exit 1
		fi
		rm -rf "$tmpdir" || true
	fi
}

install_shellcheck_binary() {
	local SHELLCHECK_VERSION="$1"
	local tmpdir os arch tar_url
	tmpdir=$(mktemp -d)
	os=$(uname -s | tr '[:upper:]' '[:lower:]')
	arch=$(uname -m)
	case "$arch" in
	x86_64 | amd64) arch="x86_64" ;;
	aarch64 | arm64) arch="aarch64" ;;
	esac
	tar_url="https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.${os}.${arch}.tar.xz"
	if download_with_retries "$tar_url" "$tmpdir/shellcheck.tar.xz" 3; then
		tar -xJf "$tmpdir/shellcheck.tar.xz" -C "$tmpdir"
		cp "$tmpdir/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" "$BIN_DIR/shellcheck"
		chmod +x "$BIN_DIR/shellcheck"
		rm -rf "$tmpdir"
		return 0
	else
		rm -rf "$tmpdir"
		return 1
	fi
}

install_shellcheck() {
	if ! should_install "shellcheck"; then
		log_verbose "Skipping shellcheck (not in --tools filter)"
		return 0
	fi
	# Install shellcheck (shell script linter)
	echo -e "${BLUE}Installing shellcheck...${NC}"
	local SHELLCHECK_VERSION installed_version
	SHELLCHECK_VERSION=$(get_tool_version "shellcheck") || exit 1

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install shellcheck v${SHELLCHECK_VERSION}"
	elif command -v shellcheck &>/dev/null; then
		# Check if installed version meets minimum requirement
		installed_version=$(shellcheck --version 2>/dev/null | grep -oE 'version: [0-9]+\.[0-9]+\.[0-9]+' | cut -d' ' -f2)
		if [ -n "$installed_version" ]; then
			# Compare versions using portable version_ge function from utils.sh
			if version_ge "$installed_version" "$SHELLCHECK_VERSION"; then
				echo -e "${GREEN}✓ shellcheck v${installed_version} already installed (>= v${SHELLCHECK_VERSION})${NC}"
			else
				echo -e "${YELLOW}⚠ shellcheck v${installed_version} is older than required v${SHELLCHECK_VERSION}, upgrading...${NC}"
				if install_shellcheck_binary "$SHELLCHECK_VERSION"; then
					echo -e "${GREEN}✓ shellcheck upgraded to v${SHELLCHECK_VERSION}${NC}"
				else
					echo -e "${RED}✗ Failed to download shellcheck${NC}"
					exit 1
				fi
			fi
		else
			# Could not parse version, treat as not installed
			echo -e "${YELLOW}⚠ Could not determine shellcheck version, installing v${SHELLCHECK_VERSION}...${NC}"
			if install_shellcheck_binary "$SHELLCHECK_VERSION"; then
				echo -e "${GREEN}✓ shellcheck installed successfully${NC}"
			else
				echo -e "${RED}✗ Failed to download shellcheck${NC}"
				exit 1
			fi
		fi
	else
		if install_shellcheck_binary "$SHELLCHECK_VERSION"; then
			echo -e "${GREEN}✓ shellcheck installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to download shellcheck${NC}"
			exit 1
		fi
	fi
}

install_dotenv_linter_binary() {
	local DOTENV_LINTER_VERSION="$1"
	local tmpdir os arch tar_url
	tmpdir=$(mktemp -d)
	os=$(uname -s | tr '[:upper:]' '[:lower:]')
	arch=$(uname -m)
	# dotenv-linter uses aarch64 on linux but arm64 on darwin
	case "$os" in
	darwin)
		case "$arch" in
		x86_64 | amd64) arch="x86_64" ;;
		aarch64 | arm64) arch="arm64" ;;
		esac
		;;
	*)
		case "$arch" in
		x86_64 | amd64) arch="x86_64" ;;
		aarch64 | arm64) arch="aarch64" ;;
		esac
		;;
	esac
	tar_url="https://github.com/dotenv-linter/dotenv-linter/releases/download/v${DOTENV_LINTER_VERSION}/dotenv-linter-${os}-${arch}.tar.gz"
	if download_with_retries "$tar_url" "$tmpdir/dotenv-linter.tar.gz" 3; then
		tar -xzf "$tmpdir/dotenv-linter.tar.gz" -C "$tmpdir"
		cp "$tmpdir/dotenv-linter" "$BIN_DIR/dotenv-linter"
		chmod +x "$BIN_DIR/dotenv-linter"
		rm -rf "$tmpdir"
		return 0
	else
		rm -rf "$tmpdir"
		return 1
	fi
}

install_dotenv_linter() {
	if ! should_install "dotenv-linter"; then
		log_verbose "Skipping dotenv-linter (not in --tools filter)"
		return 0
	fi
	# Install dotenv-linter (.env file linter and fixer)
	echo -e "${BLUE}Installing dotenv-linter...${NC}"
	local DOTENV_LINTER_VERSION installed_version
	DOTENV_LINTER_VERSION=$(get_tool_version "dotenv-linter") || exit 1

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install dotenv-linter v${DOTENV_LINTER_VERSION}"
	elif command -v dotenv-linter &>/dev/null; then
		# Check if installed version meets minimum requirement
		installed_version=$(dotenv-linter --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
		if [ -n "$installed_version" ] && version_ge "$installed_version" "$DOTENV_LINTER_VERSION"; then
			echo -e "${GREEN}✓ dotenv-linter v${installed_version} already installed (>= v${DOTENV_LINTER_VERSION})${NC}"
		else
			echo -e "${YELLOW}⚠ Installing dotenv-linter v${DOTENV_LINTER_VERSION}...${NC}"
			if install_dotenv_linter_binary "$DOTENV_LINTER_VERSION"; then
				echo -e "${GREEN}✓ dotenv-linter installed successfully${NC}"
			else
				echo -e "${RED}✗ Failed to download dotenv-linter${NC}"
				exit 1
			fi
		fi
	else
		if install_dotenv_linter_binary "$DOTENV_LINTER_VERSION"; then
			echo -e "${GREEN}✓ dotenv-linter installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to download dotenv-linter${NC}"
			exit 1
		fi
	fi
}

install_taplo() {
	if ! should_install "taplo"; then
		log_verbose "Skipping taplo (not in --tools filter)"
		return 0
	fi
	# Install taplo (TOML linter and formatter)
	echo -e "${BLUE}Installing taplo...${NC}"
	local TAPLO_VERSION
	TAPLO_VERSION=$(get_tool_version "taplo") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install taplo v${TAPLO_VERSION}"
	elif command -v taplo &>/dev/null; then
		echo -e "${GREEN}✓ taplo already installed${NC}"
	else
		local taplo_installed=false
		local tmpdir os arch gz_url checksum_url checksum_ok expected actual cargo_bin
		tmpdir=$(mktemp -d)
		os=$(uname -s | tr '[:upper:]' '[:lower:]')
		arch=$(uname -m)
		case "$arch" in
		x86_64 | amd64) arch="x86_64" ;;
		aarch64 | arm64) arch="aarch64" ;;
		esac
		# taplo releases use format: taplo-{os}-{arch}.gz (changed from taplo-full- in v0.9+)
		gz_url="https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-${os}-${arch}.gz"
		# Check if GitHub release exists before attempting download
		if curl -sfIL "$gz_url" >/dev/null 2>&1; then
			if download_with_retries "$gz_url" "$tmpdir/taplo.gz" 3; then
				# Require checksum verification before installing
				checksum_url="${gz_url}.sha256"
				checksum_ok=false
				if download_with_retries "$checksum_url" "$tmpdir/taplo.gz.sha256" 3; then
					echo -e "${BLUE}Verifying checksum for taplo...${NC}"
					expected=$(awk '{print $1}' "$tmpdir/taplo.gz.sha256" | head -n1)
					if command -v sha256sum >/dev/null 2>&1; then
						actual=$(sha256sum "$tmpdir/taplo.gz" | awk '{print $1}')
					elif command -v shasum >/dev/null 2>&1; then
						actual=$(shasum -a 256 "$tmpdir/taplo.gz" | awk '{print $1}')
					else
						echo -e "${RED}✗ No sha256sum or shasum available for checksum verification${NC}"
					fi
					if [ "$expected" = "$actual" ]; then
						echo -e "${GREEN}✓ Checksum verified${NC}"
						checksum_ok=true
					else
						echo -e "${RED}✗ Checksum mismatch for taplo (expected: $expected, got: $actual)${NC}"
					fi
					rm -f "$tmpdir/taplo.gz.sha256" || true
				else
					echo -e "${RED}✗ Failed to download checksum file for taplo${NC}"
				fi
				# Only install if checksum verification passed
				if [ "$checksum_ok" = true ]; then
					gunzip -c "$tmpdir/taplo.gz" >"$BIN_DIR/taplo"
					chmod +x "$BIN_DIR/taplo"
					echo -e "${GREEN}✓ taplo installed successfully${NC}"
					taplo_installed=true
				fi
			fi
		else
			echo -e "${YELLOW}⚠ GitHub release for taplo v${TAPLO_VERSION} not available${NC}"
		fi
		rm -rf "$tmpdir"

		# Fallback to cargo if binary download failed
		if [ "$taplo_installed" = false ]; then
			echo -e "${BLUE}Attempting fallback installation via cargo...${NC}"
			if command -v cargo &>/dev/null; then
				echo -e "${BLUE}Installing taplo via cargo...${NC}"
				if cargo install taplo-cli --locked --version "$TAPLO_VERSION"; then
					# Derive cargo bin directory from CARGO_HOME or default
					cargo_bin="${CARGO_HOME:-$HOME/.cargo}/bin"
					# Check for executable taplo in cargo bin, fall back to PATH
					if [ -x "$cargo_bin/taplo" ]; then
						cp "$cargo_bin/taplo" "$BIN_DIR/taplo"
						chmod +x "$BIN_DIR/taplo"
						echo -e "${GREEN}✓ taplo installed via cargo${NC}"
						taplo_installed=true
					elif command -v taplo &>/dev/null; then
						echo -e "${GREEN}✓ taplo installed via cargo (found on PATH)${NC}"
						taplo_installed=true
					fi
				fi
			fi
		fi

		if [ "$taplo_installed" = false ]; then
			echo -e "${RED}✗ Failed to install taplo${NC}"
			exit 1
		fi
	fi
}

install_binary_tools() {
	install_hadolint
	install_gitleaks
	install_osv_scanner
	install_actionlint
	install_shfmt
	install_vale
	install_shellcheck
	install_dotenv_linter
	install_taplo
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
		cat <<'EOF'
Usage: binary-tools.sh [--help]
Install prebuilt binary tools (hadolint, gitleaks, osv-scanner, actionlint,
shfmt, vale, shellcheck, dotenv-linter, taplo).

Respects env: DRY_RUN, TOOL_FILTER, BIN_DIR, INSTALL_MODE, VERBOSE.
Usually invoked via scripts/utils/install-tools.sh.
EOF
		exit 0
	fi
	ensure_bin_dir
	install_binary_tools "$@"
fi
