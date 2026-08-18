#!/usr/bin/env bash
set -euo pipefail

# install-semgrep.sh - Install semgrep from the committed lockfile into an
# isolated venv and expose the binary on PATH.
#
# Semgrep is not part of lintro's shared Python resolver (#2104). This script
# is the only supported install path for the pinned version in
# requirements-semgrep.txt. Ad-hoc unpinned installs (`uv tool install
# semgrep` with no lock) must not replace this for CI/Docker.
#
# Usage:
#   ./scripts/utils/install-semgrep.sh [--help] [--dry-run] [--verbose]
#                                      [--local|--docker]
#                                      [--venv PATH] [--bin-dir PATH]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REQUIREMENTS_FILE="${SEMGREP_REQUIREMENTS:-$PROJECT_ROOT/requirements-semgrep.txt}"

DRY_RUN=0
VERBOSE="${VERBOSE:-0}"
INSTALL_MODE="local"
VENV_OVERRIDE=""
BIN_DIR_OVERRIDE=""

usage() {
	cat <<'EOF'
Usage: install-semgrep.sh [--help] [--dry-run] [--verbose]
                          [--local|--docker]
                          [--venv PATH] [--bin-dir PATH]

Install the lockfile-pinned semgrep from requirements-semgrep.txt into
an isolated virtualenv and symlink the `semgrep` binary onto PATH.

Options:
  --help, -h     Show this help message
  --dry-run      Show what would be done without executing
  --verbose      Enable verbose output
  --local        Install under ~/.local (default)
  --docker       Install under /opt/semgrep-venv and /usr/local/bin
  --venv PATH    Override the virtualenv directory
  --bin-dir PATH Override the directory that receives the `semgrep` symlink

Environment:
  SEMGREP_REQUIREMENTS  Override the requirements file path
  SEMGREP_VENV          Override the virtualenv directory (same as --venv)
  SEMGREP_BIN_DIR       Override the bin directory (same as --bin-dir)
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--help | -h)
		usage
		exit 0
		;;
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--verbose)
		VERBOSE=1
		shift
		;;
	--local | local)
		INSTALL_MODE="local"
		shift
		;;
	--docker | docker)
		INSTALL_MODE="docker"
		shift
		;;
	--venv)
		if [[ -z "${2:-}" || "$2" == --* ]]; then
			echo "Error: --venv requires a path" >&2
			exit 1
		fi
		VENV_OVERRIDE="$2"
		shift 2
		;;
	--bin-dir)
		if [[ -z "${2:-}" || "$2" == --* ]]; then
			echo "Error: --bin-dir requires a path" >&2
			exit 1
		fi
		BIN_DIR_OVERRIDE="$2"
		shift 2
		;;
	*)
		echo "Error: unknown argument: $1" >&2
		usage >&2
		exit 1
		;;
	esac
done

if [ "$INSTALL_MODE" = "docker" ]; then
	DEFAULT_VENV="/opt/semgrep-venv"
	DEFAULT_BIN_DIR="/usr/local/bin"
else
	DEFAULT_VENV="${XDG_DATA_HOME:-$HOME/.local/share}/lintro/semgrep-venv"
	DEFAULT_BIN_DIR="$HOME/.local/bin"
fi

VENV_DIR="${VENV_OVERRIDE:-${SEMGREP_VENV:-$DEFAULT_VENV}}"
BIN_DIR="${BIN_DIR_OVERRIDE:-${SEMGREP_BIN_DIR:-$DEFAULT_BIN_DIR}}"

log_verbose() {
	if [ "$VERBOSE" -eq 1 ]; then
		echo "[install-semgrep] $*"
	fi
}

if [ ! -f "$REQUIREMENTS_FILE" ]; then
	echo "Error: requirements file not found: $REQUIREMENTS_FILE" >&2
	exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
	echo "Error: uv is required to install the isolated semgrep environment" >&2
	exit 1
fi

log_verbose "mode=$INSTALL_MODE venv=$VENV_DIR bin=$BIN_DIR"
log_verbose "requirements=$REQUIREMENTS_FILE"

if [ "$DRY_RUN" -eq 1 ]; then
	echo "[DRY-RUN] Would create venv at $VENV_DIR"
	echo "[DRY-RUN] Would uv pip sync $REQUIREMENTS_FILE into that venv"
	echo "[DRY-RUN] Would uv pip check --python $VENV_DIR/bin/python"
	echo "[DRY-RUN] Would symlink $VENV_DIR/bin/semgrep -> $BIN_DIR/semgrep"
	exit 0
fi

mkdir -p "$BIN_DIR"
mkdir -p "$(dirname "$VENV_DIR")"

# UV_SYSTEM_PYTHON=1 in the tools image would otherwise target the system
# interpreter. Force the isolated venv for every uv invocation here.
export UV_SYSTEM_PYTHON=0

if [ ! -x "$VENV_DIR/bin/python" ]; then
	uv venv "$VENV_DIR"
fi

uv pip sync --python "$VENV_DIR/bin/python" "$REQUIREMENTS_FILE"
uv pip check --python "$VENV_DIR/bin/python"

if [ ! -x "$VENV_DIR/bin/semgrep" ]; then
	echo "Error: semgrep binary missing after pip sync: $VENV_DIR/bin/semgrep" >&2
	exit 1
fi

ln -sfn "$VENV_DIR/bin/semgrep" "$BIN_DIR/semgrep"

echo "Installed isolated semgrep from $REQUIREMENTS_FILE"
echo "  venv: $VENV_DIR"
echo "  bin:  $BIN_DIR/semgrep"
