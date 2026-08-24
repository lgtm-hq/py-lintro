#!/usr/bin/env bash
# Portable chdir+exec for hyperfine --shell=none.
#
# GNU ``env -C`` is not available on stock macOS ``/usr/bin/env``, so both the
# lintro and direct-tool sides go through this wrapper. The extra process is
# identical on both sides and cancels out of relative overhead.
set -euo pipefail

if [[ $# -lt 2 ]]; then
	echo "usage: run-in-dir.sh DIR COMMAND [ARGS...]" >&2
	exit 2
fi

dir="$1"
shift
cd "${dir}"
exec "$@"
