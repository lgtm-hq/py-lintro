#!/usr/bin/env bash
# Python ecosystem tool installers (pip/uv packages).
# Sourced by install-tools.sh or runnable directly.
set -euo pipefail

_PYTHON_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_helpers.sh disable=SC1091
source "$_PYTHON_TOOLS_DIR/_helpers.sh"

install_ruff() {
	if ! should_install "ruff"; then
		log_verbose "Skipping ruff (not in --tools filter)"
		return 0
	fi
	# Install ruff (Python linting and formatting)
	echo -e "${BLUE}Installing ruff...${NC}"
	local RUFF_VERSION
	RUFF_VERSION=$(get_tool_version "ruff") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install ruff==${RUFF_VERSION}"
	elif install_python_package "ruff" "$RUFF_VERSION"; then
		echo -e "${GREEN}✓ ruff installed successfully${NC}"
	else
		if command -v brew &>/dev/null; then
			echo -e "${YELLOW}Trying Homebrew for ruff...${NC}"
			if [ "$DRY_RUN" -eq 1 ]; then
				log_info "[DRY-RUN] Would install ruff via brew"
			else
				brew install ruff || {
					echo -e "${RED}✗ Failed to install ruff via Homebrew${NC}"
					exit 1
				}
			fi
			echo -e "${GREEN}✓ ruff installed successfully via Homebrew${NC}"
		else
			echo -e "${RED}✗ Cannot install ruff automatically; please install via your package manager.${NC}"
			exit 1
		fi
	fi
}

install_black() {
	if ! should_install "black"; then
		log_verbose "Skipping black (not in --tools filter)"
		return 0
	fi
	# Install black (Python code formatter)
	echo -e "${BLUE}Installing black...${NC}"
	local BLACK_VERSION
	BLACK_VERSION=$(get_tool_version "black") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install black==${BLACK_VERSION}"
	elif install_python_package "black" "$BLACK_VERSION"; then
		echo -e "${GREEN}✓ black installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install black${NC}"
		exit 1
	fi
}

install_bandit() {
	if ! should_install "bandit"; then
		log_verbose "Skipping bandit (not in --tools filter)"
		return 0
	fi
	# Install bandit (Python security linter)
	echo -e "${BLUE}Installing bandit...${NC}"
	local BANDIT_VERSION
	BANDIT_VERSION=$(get_tool_version "bandit") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install bandit==${BANDIT_VERSION}"
	elif install_python_package "bandit" "$BANDIT_VERSION"; then
		echo -e "${GREEN}✓ bandit installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install bandit${NC}"
		exit 1
	fi
}

install_mypy() {
	if ! should_install "mypy"; then
		log_verbose "Skipping mypy (not in --tools filter)"
		return 0
	fi
	# Install mypy (Python type checker)
	echo -e "${BLUE}Installing mypy...${NC}"
	local MYPY_VERSION
	MYPY_VERSION=$(get_tool_version "mypy") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install mypy==${MYPY_VERSION}"
	elif install_python_package "mypy" "$MYPY_VERSION"; then
		echo -e "${GREEN}✓ mypy installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install mypy${NC}"
		exit 1
	fi
}

install_semgrep() {
	if ! should_install "semgrep"; then
		log_verbose "Skipping semgrep (not in --tools filter)"
		return 0
	fi
	# Install semgrep (security scanner)
	echo -e "${BLUE}Installing semgrep...${NC}"
	local SEMGREP_VERSION
	SEMGREP_VERSION=$(get_tool_version "semgrep") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install semgrep==${SEMGREP_VERSION}"
	elif install_python_package "semgrep" "$SEMGREP_VERSION"; then
		echo -e "${GREEN}✓ semgrep installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install semgrep${NC}"
		exit 1
	fi
}

install_pip_audit() {
	if ! should_install "pip-audit"; then
		log_verbose "Skipping pip-audit (not in --tools filter)"
		return 0
	fi
	# Install pip-audit (Python dependency vulnerability scanner)
	echo -e "${BLUE}Installing pip-audit...${NC}"
	local PIP_AUDIT_VERSION
	PIP_AUDIT_VERSION=$(get_tool_version "pip-audit") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install pip-audit==${PIP_AUDIT_VERSION}"
	elif install_python_package "pip-audit" "$PIP_AUDIT_VERSION"; then
		echo -e "${GREEN}✓ pip-audit installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install pip-audit${NC}"
		exit 1
	fi
}

install_yamllint() {
	if ! should_install "yamllint"; then
		log_verbose "Skipping yamllint (not in --tools filter)"
		return 0
	fi
	# Install yamllint (Python package)
	echo -e "${BLUE}Installing yamllint...${NC}"
	local YAMLLINT_VERSION
	YAMLLINT_VERSION=$(get_tool_version "yamllint") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install yamllint==${YAMLLINT_VERSION}"
	elif install_python_package "yamllint" "$YAMLLINT_VERSION"; then
		echo -e "${GREEN}✓ yamllint installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install yamllint${NC}"
		exit 1
	fi
}

install_pydoclint() {
	if ! should_install "pydoclint"; then
		log_verbose "Skipping pydoclint (not in --tools filter)"
		return 0
	fi
	# Install pydoclint (Python docstring linter)
	echo -e "${BLUE}Installing pydoclint...${NC}"
	local PYDOCLINT_VERSION
	PYDOCLINT_VERSION=$(get_tool_version "pydoclint") || exit 1

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install pydoclint==${PYDOCLINT_VERSION}"
	elif install_python_package "pydoclint" "$PYDOCLINT_VERSION"; then
		echo -e "${GREEN}✓ pydoclint installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install pydoclint${NC}"
		exit 1
	fi
}

install_sqlfluff() {
	if ! should_install "sqlfluff"; then
		log_verbose "Skipping sqlfluff (not in --tools filter)"
		return 0
	fi
	# Install sqlfluff (SQL linter and formatter)
	echo -e "${BLUE}Installing sqlfluff...${NC}"
	local SQLFLUFF_VERSION
	SQLFLUFF_VERSION=$(get_tool_version "sqlfluff") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install sqlfluff==${SQLFLUFF_VERSION}"
	elif install_python_package "sqlfluff" "$SQLFLUFF_VERSION"; then
		echo -e "${GREEN}✓ sqlfluff installed successfully${NC}"
	else
		echo -e "${RED}✗ Failed to install sqlfluff${NC}"
		exit 1
	fi
}

install_python_tools() {
	install_ruff
	install_black
	install_bandit
	install_mypy
	install_semgrep
	install_pip_audit
	install_yamllint
	install_pydoclint
	install_sqlfluff
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
		cat <<'EOF'
Usage: python-tools.sh [--help]
Install Python ecosystem tools (ruff, black, bandit, mypy, semgrep,
pip-audit, yamllint, pydoclint, sqlfluff).

Respects env: DRY_RUN, TOOL_FILTER, BIN_DIR, INSTALL_MODE, VERBOSE.
Usually invoked via scripts/utils/install-tools.sh.
EOF
		exit 0
	fi
	ensure_bin_dir
	install_python_tools "$@"
fi
