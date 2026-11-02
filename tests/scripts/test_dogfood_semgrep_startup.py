"""Exercise the dogfood scripts' Docker environment boundary without Docker."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - fixed script argv in an isolated test
import sys
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Model Docker's environment isolation and -e transport, then run a real child
# process. A runner variable alone must never satisfy this regression.
_DOCKER = """
import json
import os
import subprocess
import sys
from pathlib import Path

if sys.argv[1] == 'pull' or '--entrypoint' in sys.argv:
    sys.exit(0)
args = sys.argv[2:]
env = {}
while args:
    arg = args.pop(0)
    if arg == '--rm':
        continue
    if arg in ('-v', '-w', '--user'):
        args.pop(0)
        continue
    if arg == '-e':
        key, sep, value = args.pop(0).partition('=')
        if sep or key in os.environ:
            env[key] = value if sep else os.environ[key]
        continue
    break
subprocess.run([sys.executable, '-c',
    'import json, os; print(json.dumps(dict(os.environ)))'], env=env, check=True)
if '--output' in args:
    Path(args[args.index('--output') + 1]).write_text(
        json.dumps({'summary': {'total_issues': 0}, 'results': []}))
"""


@pytest.mark.parametrize("script", ["dogfood-changed-files", "dogfood-skip-gate"])
def test_dogfood_container_disables_only_semgrep_update_check(
    tmp_path: Path,
    script: str,
) -> None:
    """Child processes receive the update opt-out while auto metrics stay usable."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(f"#!{sys.executable}\n{_DOCKER}")
    docker.chmod(0o755)
    # An unresolved diff deliberately exercises changed-files' full fallback.
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\nexit 1\n")
    git.chmod(0o755)
    output = tmp_path / "github-output"
    result = subprocess.run(  # nosec B603 - fixed local script, shell=False
        [str(_REPO_ROOT / "scripts" / "ci" / f"{script}.sh")],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": os.environ["HOME"],
            "LINTRO_IMAGE": "offline:2521",
            "BASE_REF": "main",
            "MAP_HOST_USER": "false",
            "GITHUB_OUTPUT": str(output),
            # Deliberately hostile runner values must not leak into Docker.
            "SEMGREP_ENABLE_VERSION_CHECK": "1",
            "SEMGREP_SEND_METRICS": "off",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    stdout = (
        (tmp_path / "dogfood-skip-report.json.stdout").read_text()
        if script == "dogfood-skip-gate"
        else result.stdout
    )
    child_env = json.loads(
        next(line for line in stdout.splitlines() if line.startswith("{")),
    )
    assert_that(child_env.get("SEMGREP_ENABLE_VERSION_CHECK")).is_equal_to("0")
    # --config auto requires metrics to remain enabled/auto (#2521 correction).
    assert_that(child_env).does_not_contain_key("SEMGREP_SEND_METRICS")
    assert_that(output.read_text()).contains("status=passed", "exit-code=0")
