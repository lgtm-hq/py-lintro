"""Guards the repo-level mypy cache location (#2297)."""

from __future__ import annotations

import subprocess  # nosec B404 - runs mypy with a fixed argv; shell=False
import sys
import tomllib
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_ANCHOR = "$MYPY_CONFIG_FILE_DIR"


def _mypy_config() -> dict[str, object]:
    """Return the ``[tool.mypy]`` table from the repo pyproject.

    Returns:
        The parsed mypy configuration table.
    """
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    tool = data["tool"]
    assert_that(tool).contains_key("mypy")
    return dict(tool["mypy"])


def test_cache_dir_is_anchored_to_the_config_directory() -> None:
    """``cache_dir`` must not be a bare, cwd-relative path.

    mypy expands ``cache_dir`` with ``expanduser``/``expandvars`` and then
    resolves it against the process working directory. A bare
    ``".mypy_cache"`` is therefore both mypy's own default and a no-op: an
    invocation started from a subdirectory still scatters a cache there.
    ``$MYPY_CONFIG_FILE_DIR`` expands to the directory holding pyproject.toml,
    which pins the cache to the repo root from any cwd (#2297).
    """
    cache_dir = _mypy_config().get("cache_dir")

    assert_that(cache_dir).is_not_none()
    assert_that(str(cache_dir)).starts_with(_ANCHOR)
    assert_that(str(cache_dir)).is_equal_to(f"{_ANCHOR}/.mypy_cache")


def test_running_mypy_from_a_subdirectory_writes_no_nested_cache() -> None:
    """A non-root cwd must reuse the repo-root cache, not create its own.

    This is the behaviour the config exists for, so assert it end to end
    rather than trusting the setting: run mypy from a scratch subdirectory of
    the repo and require that no ``.mypy_cache`` appears beside it.
    """
    workdir = _REPO_ROOT / "build" / "mypy-cache-probe"
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / "probe.py"
    target.write_text("x: int = 1\n", encoding="utf-8")
    nested_cache = workdir / ".mypy_cache"
    anchored_cache = _REPO_ROOT / ".mypy_cache"
    try:
        result = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                str(_PYPROJECT),
                "--no-error-summary",
                "probe.py",
            ],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
        # Prove mypy actually ran before trusting the absence below: a probe
        # that never launched (missing module, bad argv) would write no cache
        # anywhere and pass vacuously. mypy exits 0 (clean) or 1 (findings);
        # anything else is a launch failure, not a verdict.
        assert_that(result.returncode).described_as(
            f"mypy did not run: {result.stderr}",
        ).is_in(0, 1)
        # Positive evidence: the cache landed at the anchored location...
        assert_that(anchored_cache.is_dir()).described_as(
            "mypy wrote no cache at the configured anchor",
        ).is_true()
        # ...and not beside the working directory.
        assert_that(nested_cache.exists()).described_as(
            "mypy scattered a cache into its working directory",
        ).is_false()
    finally:
        target.unlink(missing_ok=True)
        workdir.rmdir()
