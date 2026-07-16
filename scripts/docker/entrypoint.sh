#!/usr/bin/env bash
# entrypoint.sh - Flexible Docker entrypoint for lintro
#
# This entrypoint supports multiple invocation styles:
#   - `lintro ...` - runs lintro with arguments
#   - `python ...` - runs Python with arguments
#   - `<any other command>` - treated as lintro arguments
#
# The venv interpreter is always used for consistency.

set -e

# Verbose diagnostics for volume/permission debugging. Enable with
# LINTRO_VERBOSE=1 (issue #822, item 6). All output goes to stderr so it never
# pollutes lintro's machine-readable stdout.
log_verbose() {
	if [ "${LINTRO_VERBOSE:-0}" = "1" ]; then
		echo "[lintro][verbose] $*" >&2
	fi
}

# Auto-detect volume owner and drop root privileges.
# When running as root (the default after removing USER from the Dockerfile),
# detect the UID/GID that owns /code and re-exec as that user via gosu.
# This ensures the container can write node_modules into the mounted volume
# without consumers needing --user flags.
# If /code doesn't exist (no volume mount), fall back to the built-in lintro user.
if [ "$(id -u)" = '0' ]; then
	if [ -d "/code" ]; then
		CODE_UID=$(stat -c '%u' /code 2>/dev/null || echo "1000")
		CODE_GID=$(stat -c '%g' /code 2>/dev/null || echo "1000")
	else
		# No volume mount — fall back to the lintro user
		CODE_UID=$(id -u lintro 2>/dev/null || echo "1000")
		CODE_GID=$(id -g lintro 2>/dev/null || echo "1000")
	fi
	# Only re-exec when the target UID/GID differs from current.
	# This prevents an infinite loop when /code is owned by root (0:0).
	CUR_UID=$(id -u)
	CUR_GID=$(id -g)
	if [ "$CODE_UID" != "$CUR_UID" ] || [ "$CODE_GID" != "$CUR_GID" ]; then
		log_verbose "dropping root: re-exec via gosu as ${CODE_UID}:${CODE_GID}"
		exec gosu "$CODE_UID:$CODE_GID" "$0" "$@"
	fi
fi

log_verbose "running as UID:GID $(id -u):$(id -g) ($(id -un 2>/dev/null || echo unknown))"

# Ensure writable directories for mapped users (e.g., --user "$(id -u):$(id -g)")
# When Docker runs with a mapped host user, tool-specific directories may not be
# writable. Redirect them to /tmp to avoid permission errors.
# This also handles the post-gosu re-exec, where the matched UID typically
# won't have a home directory.
if [ ! -d "$HOME" ] || [ ! -w "$HOME" ]; then
	export HOME="/tmp"
fi

# Cargo needs a writable CARGO_HOME for registry cache and index
# Keep original bin dir in PATH so cargo/clippy/rustfmt binaries are still found
if [ -n "${CARGO_HOME:-}" ] && [ ! -w "${CARGO_HOME}" ]; then
	CARGO_BIN="${CARGO_HOME}/bin"
	log_verbose "CARGO_HOME '${CARGO_HOME}' not writable; redirecting to /tmp/.cargo"
	export CARGO_HOME="/tmp/.cargo"
	mkdir -p "${CARGO_HOME}/bin"
	# Preserve access to pre-installed cargo binaries and new CARGO_HOME/bin
	if [ -d "$CARGO_BIN" ]; then
		export PATH="${CARGO_HOME}/bin:${CARGO_BIN}:${PATH}"
	else
		export PATH="${CARGO_HOME}/bin:${PATH}"
	fi
fi

# Bun needs a writable BUN_INSTALL for cache and temp files during install
# Keep original bin dir in PATH so pre-installed bun binaries are still found
if [ -n "${BUN_INSTALL:-}" ] && [ ! -w "${BUN_INSTALL}" ]; then
	BUN_BIN="${BUN_INSTALL}/bin"
	log_verbose "BUN_INSTALL '${BUN_INSTALL}' not writable; redirecting to /tmp/.bun"
	export BUN_INSTALL="/tmp/.bun"
	mkdir -p "${BUN_INSTALL}/bin"
	if [ -d "$BUN_BIN" ]; then
		export PATH="${BUN_INSTALL}/bin:${BUN_BIN}:${PATH}"
	else
		export PATH="${BUN_INSTALL}/bin:${PATH}"
	fi
fi

# Handle --help
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
	cat <<'EOF'
entrypoint.sh - Docker entrypoint for lintro container

USAGE:
    docker run lintro [COMMAND] [ARGS...]

COMMANDS:
    lintro [ARGS]     Run lintro with specified arguments
    python [ARGS]     Run Python interpreter with arguments
    <any>             Treated as lintro arguments

EXAMPLES:
    docker run lintro check --tools ruff
    docker run lintro lintro list-tools
    docker run lintro python -c "print('hello')"

The virtual environment Python interpreter (/app/.venv/bin/python)
is always used for consistency.
EOF
	exit 0
fi

VENV_PYTHON="/app/.venv/bin/python"

# Change to /code if it exists and has content (user's mounted project)
# This ensures lintro scans the user's code, not /app (lintro's installation)
if [ -d "/code" ] && [ "$(ls -A /code 2>/dev/null)" ]; then
	cd /code
fi

# Auto-install Node.js dependencies if explicitly enabled via environment variable
# This enables seamless tsc support in Docker without manual intervention
# Security: Requires explicit opt-in via LINTRO_AUTO_INSTALL_DEPS=1
# Security: Uses --ignore-scripts to prevent lifecycle script execution
if [ "${LINTRO_AUTO_INSTALL_DEPS:-0}" = "1" ] && [ -f "package.json" ] && [ ! -d "node_modules" ]; then
	echo "[lintro] Installing Node.js dependencies (LINTRO_AUTO_INSTALL_DEPS=1)..."
	install_success=false
	# Stream install output (do not buffer the full log in memory). Verbose
	# mode still surfaces the command line via log_verbose.
	if command -v bun &>/dev/null; then
		# Use --frozen-lockfile for reproducibility, --ignore-scripts for security
		log_verbose "install command: bun install --frozen-lockfile --ignore-scripts"
		if bun install --frozen-lockfile --ignore-scripts ||
			bun install --ignore-scripts; then
			install_success=true
		fi
	elif command -v npm &>/dev/null; then
		# Use npm ci for reproducibility (requires package-lock.json), --ignore-scripts for security
		log_verbose "install command: npm ci --ignore-scripts"
		if npm ci --ignore-scripts ||
			npm install --ignore-scripts; then
			install_success=true
		fi
	else
		echo "[lintro] Warning: No package manager (bun/npm) found"
	fi
	if [ "$install_success" = true ]; then
		echo "[lintro] Node.js dependencies installed."
	else
		echo "[lintro] Warning: Failed to install Node.js dependencies (may be read-only mount)"
	fi
fi

if [ "$1" = "lintro" ]; then
	shift
	exec "$VENV_PYTHON" -m lintro "$@"
elif [ "$1" = "python" ]; then
	shift
	exec "$VENV_PYTHON" "$@"
else
	# Default: treat all arguments as lintro arguments
	exec "$VENV_PYTHON" -m lintro "$@"
fi
