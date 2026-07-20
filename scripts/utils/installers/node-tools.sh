#!/usr/bin/env bash
# Node/bun ecosystem tool installers.
# Sourced by install-tools.sh or runnable directly.
set -euo pipefail

_NODE_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_helpers.sh disable=SC1091
source "$_NODE_TOOLS_DIR/_helpers.sh"

install_commitlint() {
	if ! should_install "commitlint"; then
		log_verbose "Skipping commitlint (not in --tools filter)"
		return 0
	fi
	# Install commitlint via bun (Conventional Commits message linting).
	# The shared config package is installed alongside so user configs that
	# extend @commitlint/config-conventional resolve.
	echo -e "${BLUE}Installing commitlint...${NC}"

	# Ensure bun is available
	if ! ensure_bun_installed; then
		exit 1
	fi

	# Read versions from _tool_versions.py (single source of truth).
	# Package alias: "@commitlint/cli" -> ToolName.COMMITLINT
	local COMMITLINT_VERSION COMMITLINT_CONFIG_VERSION
	COMMITLINT_VERSION=$(get_tool_version "@commitlint/cli") || exit 1
	COMMITLINT_CONFIG_VERSION=$(get_tool_version "@commitlint/config-conventional") || exit 1

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install @commitlint/cli@${COMMITLINT_VERSION} and @commitlint/config-conventional@${COMMITLINT_CONFIG_VERSION} globally via bun"
	elif bun add -g "@commitlint/cli@${COMMITLINT_VERSION}" "@commitlint/config-conventional@${COMMITLINT_CONFIG_VERSION}"; then
		echo -e "${GREEN}✓ commitlint@${COMMITLINT_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install commitlint${NC}"
		exit 1
	fi
}

install_prettier() {
	if ! should_install "prettier"; then
		log_verbose "Skipping prettier (not in --tools filter)"
		return 0
	fi
	# Install prettier via bun (JavaScript/JSON formatting)
	echo -e "${BLUE}Installing prettier...${NC}"

	# Ensure bun is available
	if ! ensure_bun_installed; then
		exit 1
	fi

	# Read prettier version from _tool_versions.py (single source of truth)
	local PRETTIER_VERSION
	PRETTIER_VERSION=$(get_tool_version "prettier") || exit 1

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install prettier@${PRETTIER_VERSION} globally via bun"
	elif bun add -g "prettier@${PRETTIER_VERSION}"; then
		echo -e "${GREEN}✓ prettier@${PRETTIER_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install prettier${NC}"
		exit 1
	fi
}

install_markdownlint() {
	if ! should_install "markdownlint" && ! should_install "markdownlint-cli2"; then
		log_verbose "Skipping markdownlint-cli2 (not in --tools filter)"
		return 0
	fi
	# Install markdownlint-cli2 via bun (Markdown linting)
	echo -e "${BLUE}Installing markdownlint-cli2...${NC}"

	# Ensure bun is available (should already be installed for prettier)
	if ! ensure_bun_installed; then
		exit 1
	fi

	# Read markdownlint-cli2 version from _tool_versions.py (single source of truth)
	# Uses package alias: "markdownlint-cli2" -> ToolName.MARKDOWNLINT
	local MARKDOWNLINT_VERSION
	MARKDOWNLINT_VERSION=$(get_tool_version "markdownlint-cli2") || exit 1

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install markdownlint-cli2@${MARKDOWNLINT_VERSION} globally via bun"
	elif bun add -g "markdownlint-cli2@${MARKDOWNLINT_VERSION}"; then
		echo -e "${GREEN}✓ markdownlint-cli2@${MARKDOWNLINT_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install markdownlint-cli2${NC}"
		exit 1
	fi
}

install_oxlint() {
	if ! should_install "oxlint"; then
		log_verbose "Skipping oxlint (not in --tools filter)"
		return 0
	fi
	# Install oxlint via bun (JavaScript/TypeScript linting)
	echo -e "${BLUE}Installing oxlint...${NC}"

	# Ensure bun is available (should already be installed for prettier)
	if ! ensure_bun_installed; then
		exit 1
	fi

	local OXLINT_VERSION
	OXLINT_VERSION=$(get_tool_version "oxlint") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install oxlint@${OXLINT_VERSION} globally via bun"
	elif bun add -g "oxlint@${OXLINT_VERSION}"; then
		echo -e "${GREEN}✓ oxlint@${OXLINT_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install oxlint${NC}"
		exit 1
	fi
}

install_oxfmt() {
	if ! should_install "oxfmt"; then
		log_verbose "Skipping oxfmt (not in --tools filter)"
		return 0
	fi
	# Install oxfmt via bun (JavaScript/TypeScript formatting)
	echo -e "${BLUE}Installing oxfmt...${NC}"

	if ! ensure_bun_installed; then
		exit 1
	fi

	local OXFMT_VERSION
	OXFMT_VERSION=$(get_tool_version "oxfmt") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install oxfmt@${OXFMT_VERSION} globally via bun"
	elif bun add -g "oxfmt@${OXFMT_VERSION}"; then
		echo -e "${GREEN}✓ oxfmt@${OXFMT_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install oxfmt${NC}"
		exit 1
	fi
}

install_stylelint() {
	if ! should_install "stylelint"; then
		log_verbose "Skipping stylelint (not in --tools filter)"
		return 0
	fi
	# Install stylelint via bun (CSS/SCSS/Less linting)
	echo -e "${BLUE}Installing stylelint...${NC}"

	if ! ensure_bun_installed; then
		exit 1
	fi

	local STYLELINT_VERSION
	STYLELINT_VERSION=$(get_tool_version "stylelint") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install stylelint@${STYLELINT_VERSION} globally via bun"
	elif bun add -g "stylelint@${STYLELINT_VERSION}"; then
		echo -e "${GREEN}✓ stylelint@${STYLELINT_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install stylelint${NC}"
		exit 1
	fi
}

install_tsc() {
	if ! should_install "tsc"; then
		log_verbose "Skipping tsc (not in --tools filter)"
		return 0
	fi
	# Install typescript via bun (TypeScript compiler)
	echo -e "${BLUE}Installing typescript...${NC}"

	if ! ensure_bun_installed; then
		exit 1
	fi

	local TYPESCRIPT_VERSION
	TYPESCRIPT_VERSION=$(get_tool_version "typescript") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install typescript@${TYPESCRIPT_VERSION} globally via bun"
	elif bun add -g "typescript@${TYPESCRIPT_VERSION}"; then
		echo -e "${GREEN}✓ typescript@${TYPESCRIPT_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install typescript${NC}"
		exit 1
	fi
}

install_astro() {
	if ! should_install "astro"; then
		log_verbose "Skipping astro (not in --tools filter)"
		return 0
	fi
	# Install astro and @astrojs/check via bun (Astro type checking)
	echo -e "${BLUE}Installing astro and @astrojs/check...${NC}"

	if ! ensure_bun_installed; then
		exit 1
	fi

	local ASTRO_VERSION ASTRO_CHECK_VERSION
	ASTRO_VERSION=$(get_tool_version "astro") || exit 1
	ASTRO_CHECK_VERSION=$(get_tool_version "@astrojs/check") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install astro@${ASTRO_VERSION} and @astrojs/check@${ASTRO_CHECK_VERSION} globally via bun"
	elif bun add -g "astro@${ASTRO_VERSION}" "@astrojs/check@${ASTRO_CHECK_VERSION}"; then
		echo -e "${GREEN}✓ astro@${ASTRO_VERSION} and @astrojs/check@${ASTRO_CHECK_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install astro and @astrojs/check${NC}"
		exit 1
	fi
}

install_svelte_check() {
	if ! should_install "svelte-check"; then
		log_verbose "Skipping svelte-check (not in --tools filter)"
		return 0
	fi
	# Install svelte-check via bun (Svelte type checking)
	echo -e "${BLUE}Installing svelte-check...${NC}"

	if ! ensure_bun_installed; then
		exit 1
	fi

	local SVELTE_CHECK_VERSION
	SVELTE_CHECK_VERSION=$(get_tool_version "svelte-check") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install svelte-check@${SVELTE_CHECK_VERSION} globally via bun"
	elif bun add -g "svelte-check@${SVELTE_CHECK_VERSION}"; then
		echo -e "${GREEN}✓ svelte-check@${SVELTE_CHECK_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install svelte-check${NC}"
		exit 1
	fi
}

install_vue_tsc() {
	if ! should_install "vue-tsc"; then
		log_verbose "Skipping vue-tsc (not in --tools filter)"
		return 0
	fi
	# Install vue-tsc via bun (Vue TypeScript type checking)
	echo -e "${BLUE}Installing vue-tsc...${NC}"

	if ! ensure_bun_installed; then
		exit 1
	fi

	local VUE_TSC_VERSION
	VUE_TSC_VERSION=$(get_tool_version "vue-tsc") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install vue-tsc@${VUE_TSC_VERSION} globally via bun"
	elif bun add -g "vue-tsc@${VUE_TSC_VERSION}"; then
		echo -e "${GREEN}✓ vue-tsc@${VUE_TSC_VERSION} installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install vue-tsc${NC}"
		exit 1
	fi
}

install_node_tools() {
	# Ensure bun once up front when any node tool is requested.
	if should_install "commitlint" || should_install "prettier" ||
		should_install "markdownlint" || should_install "markdownlint-cli2" ||
		should_install "oxlint" || should_install "oxfmt" ||
		should_install "stylelint" || should_install "tsc" ||
		should_install "astro" || should_install "svelte-check" ||
		should_install "vue-tsc"; then
		if ! ensure_bun_installed; then
			exit 1
		fi
	fi

	install_commitlint
	install_prettier
	install_markdownlint
	install_oxlint
	install_oxfmt
	install_stylelint
	install_tsc
	install_astro
	install_svelte_check
	install_vue_tsc
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
		cat <<'EOF'
Usage: node-tools.sh [--help]
Install Node/bun ecosystem tools (prettier, markdownlint-cli2, oxlint,
oxfmt, stylelint, tsc, astro, svelte-check, vue-tsc, commitlint).

Respects env: DRY_RUN, TOOL_FILTER, BIN_DIR, INSTALL_MODE, VERBOSE.
Usually invoked via scripts/utils/install-tools.sh.
EOF
		exit 0
	fi
	ensure_bin_dir
	install_node_tools "$@"
fi
