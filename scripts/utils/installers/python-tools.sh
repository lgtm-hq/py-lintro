#!/usr/bin/env bash
# Python ecosystem tool installers (pip/uv packages).
# Sourced by install-tools.sh or runnable directly.
set -euo pipefail

_PYTHON_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_helpers.sh disable=SC1091
source "$_PYTHON_TOOLS_DIR/_helpers.sh"
# install-semgrep.sh lives in scripts/utils/, one level above installers/
: "${SCRIPT_DIR:=$(cd "$_PYTHON_TOOLS_DIR/.." && pwd)}"

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
	# Isolated lockfile install (#2104): never share lintro's resolver.
	echo -e "${BLUE}Installing semgrep (isolated venv)...${NC}"
	local SEMGREP_INSTALL_ARGS=()
	if [ "$VERBOSE" -eq 1 ]; then
		SEMGREP_INSTALL_ARGS+=(--verbose)
	fi
	if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
		SEMGREP_INSTALL_ARGS+=(--docker)
	else
		SEMGREP_INSTALL_ARGS+=(--local)
	fi
	SEMGREP_INSTALL_ARGS+=(--bin-dir "$BIN_DIR")
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would run install-semgrep.sh ${SEMGREP_INSTALL_ARGS[*]}"
	elif "$SCRIPT_DIR/install-semgrep.sh" "${SEMGREP_INSTALL_ARGS[@]}"; then
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

install_import_linter() {
	local IMPORT_LINTER_VERSION IMPORT_LINTER_BIN
	if should_install "import-linter"; then
		# Install import-linter (Python import-contract checker; binary: lint-imports)
		echo -e "${BLUE}Installing import-linter...${NC}"
		IMPORT_LINTER_VERSION=$(get_tool_version "import-linter") || exit 1
		if [ "$DRY_RUN" -eq 1 ]; then
			log_info "[DRY-RUN] Would install import-linter==${IMPORT_LINTER_VERSION}"
		elif install_python_package "import-linter" "$IMPORT_LINTER_VERSION"; then
			# The import-linter distribution installs a console script named
			# lint-imports, so install_python_package (which stages by package
			# name) cannot copy it into BIN_DIR. Stage it here by binary name.
			# A successful pip install that yields no console script means the
			# tool was not delivered, so fail instead of reporting success.
			IMPORT_LINTER_BIN=$(command -v lint-imports 2>/dev/null || true)
			if [ -z "$IMPORT_LINTER_BIN" ] || [ ! -f "$IMPORT_LINTER_BIN" ]; then
				echo -e "${RED}✗ import-linter installed but lint-imports is not on PATH${NC}"
				exit 1
			fi
			if [ "$IMPORT_LINTER_BIN" != "$BIN_DIR/lint-imports" ]; then
				cp "$IMPORT_LINTER_BIN" "$BIN_DIR/lint-imports"
				chmod +x "$BIN_DIR/lint-imports"
				echo -e "${YELLOW}Copied lint-imports to $BIN_DIR${NC}"
			fi
			echo -e "${GREEN}✓ import-linter installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install import-linter${NC}"
			exit 1
		fi
	fi # import-linter
}

install_checkov() {
	# Install checkov (Terraform IaC security scanner) into its own venv.
	# checkov pins `packaging>=23.0,<24.0` while lintro pins `packaging>=25.0`,
	# so the two cannot share an environment: `uv pip install checkov` next to
	# lintro either fails to resolve or silently downgrades packaging. `uv tool
	# install` builds an isolated venv and drops only the console script on
	# PATH. The version is the exact TOOL_VERSIONS pin, so the manifest-vs-image
	# gate (which requires equality) stays satisfiable. A requirements file was
	# rejected on purpose: osv-scanner resolves requirements*.txt transitively,
	# so declaring checkov's tree here would fail this repo's own security gate.
	local CHECKOV_VERSION CHECKOV_TOOL_DIR
	if should_install "checkov"; then
		echo -e "${BLUE}Installing checkov...${NC}"
		CHECKOV_VERSION=$(get_tool_version "checkov") || exit 1
		# UV_TOOL_DIR holds the venv the shim in BIN_DIR points into. In Docker
		# the script runs as root but the container runs as `lintro`, so the
		# default (/root/.local/share/uv/tools) would leave every non-root
		# invocation with an unreadable interpreter behind a shim that is on
		# PATH. Put it under /opt like semgrep's venv, world-readable.
		if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
			CHECKOV_TOOL_DIR="/opt/uv-tools"
		else
			CHECKOV_TOOL_DIR="${UV_TOOL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools}"
		fi
		# --force is deliberate: without it `uv tool install` reports an
		# already-installed tool and leaves the old version in place, so a
		# Renovate pin bump would never take effect and the manifest-vs-image
		# equality gate would fail against a stale binary.
		if [ "$DRY_RUN" -eq 1 ]; then
			log_info "[DRY-RUN] Would run uv tool install checkov==${CHECKOV_VERSION}"
			log_info "[DRY-RUN] Would place the venv under ${CHECKOV_TOOL_DIR}"
		elif ! command -v uv &>/dev/null; then
			# Docker always ships uv, so its absence there is an image bug and
			# must stop the build. Locally every other Python tool degrades to
			# pip/brew when uv is missing, and aborting here would cancel the
			# dozen install blocks that follow, so warn and move on: the
			# verification loop still reports checkov as not found.
			if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
				echo -e "${RED}✗ checkov needs uv, which the image must provide${NC}"
				exit 1
			fi
			# A caller who named checkov asked for this tool specifically, so
			# reporting success after skipping it would be a false contract.
			# An unfiltered run only wanted "everything installable", so it
			# warns and lets the ~15 later blocks finish.
			if [ -n "$TOOL_FILTER" ]; then
				echo -e "${RED}✗ checkov needs uv; install uv first${NC}"
				exit 1
			fi
			echo -e "${YELLOW}⚠ Skipping checkov: uv is required for an isolated install${NC}"
		elif UV_TOOL_DIR="$CHECKOV_TOOL_DIR" UV_TOOL_BIN_DIR="$BIN_DIR" \
			uv tool install --force "checkov==${CHECKOV_VERSION}"; then
			# Docker builds as root and runs as `lintro`, so the venv this
			# install just created has to be world-traversable. Scoped to
			# checkov's own venv: CHECKOV_TOOL_DIR is the shared uv tool root,
			# and locally that is the user's own, holding venvs this installer
			# never created. A failure here leaves an unusable image, so it is
			# not swallowed.
			if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
				chmod -R a+rX "$CHECKOV_TOOL_DIR/checkov" || {
					echo -e "${RED}✗ Could not make the checkov venv world-readable${NC}"
					exit 1
				}
			fi
			echo -e "${GREEN}✓ checkov installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install checkov${NC}"
			exit 1
		fi
	fi # checkov
}

install_pylint() {
	local PYLINT_VERSION
	if should_install "pylint"; then
		# Install pylint (Python static analyser; duplicate-code checker)
		echo -e "${BLUE}Installing pylint...${NC}"
		PYLINT_VERSION=$(get_tool_version "pylint") || exit 1

		if [ "$DRY_RUN" -eq 1 ]; then
			log_info "[DRY-RUN] Would install pylint==${PYLINT_VERSION}"
		elif install_python_package "pylint" "$PYLINT_VERSION"; then
			echo -e "${GREEN}✓ pylint installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install pylint${NC}"
			exit 1
		fi
	fi # pylint
}

install_python_tools() {
	install_ruff
	install_black
	install_bandit
	install_mypy
	install_semgrep
	install_import_linter
	install_pip_audit
	install_checkov
	install_yamllint
	install_pydoclint
	install_pylint
	install_sqlfluff
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
		cat <<'EOF'
Usage: python-tools.sh [--help] [--dry-run] [--verbose] [--local|--docker]
                       [--tools tool1,tool2,...]
Install Python ecosystem tools (ruff, black, bandit, mypy, semgrep,
import-linter, pip-audit, checkov, yamllint, pydoclint, pylint, sqlfluff).

Respects env: DRY_RUN, TOOL_FILTER, BIN_DIR, INSTALL_MODE, VERBOSE.
Usually invoked via scripts/utils/install-tools.sh.
EOF
		exit 0
	fi
	parse_group_installer_args "$@" || exit 0
	ensure_bin_dir
	install_python_tools
fi
