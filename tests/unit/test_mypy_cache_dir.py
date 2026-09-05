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


def test_running_mypy_from_a_subdirectory_writes_no_nested_cache(
    tmp_path: Path,
) -> None:
    """A non-root cwd must write to the anchored cache, not create its own.

    This is the behaviour the setting exists for, so assert it end to end
    rather than trusting the string. The run is fully isolated in ``tmp_path``
    — the repo's own ``cache_dir`` value is copied into a throwaway config so
    the value under test stays authentic, while the cache it produces is
    freshly created here. A shared repo-root cache could otherwise satisfy the
    assertion without this invocation having written anything.

    Args:
        tmp_path: Isolated scratch root for the config, cwd and cache.
    """
    cache_dir = str(_mypy_config()["cache_dir"])
    config_dir = tmp_path / "anchor"
    workdir = tmp_path / "work"
    config_dir.mkdir()
    workdir.mkdir()
    config = config_dir / "pyproject.toml"
    config.write_text(
        f'[tool.mypy]\ncache_dir = "{cache_dir}"\n',
        encoding="utf-8",
    )
    (workdir / "probe.py").write_text("x: int = 1\n", encoding="utf-8")

    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(config),
            "--no-error-summary",
            "probe.py",
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )

    # Prove mypy ran before trusting any absence: a probe that never launched
    # would write no cache anywhere and pass vacuously. mypy exits 0 (clean)
    # or 1 (findings); anything else is a launch failure.
    assert_that(result.returncode).described_as(
        f"mypy did not run: {result.stderr}",
    ).is_in(0, 1)
    # The cache resolved against the config's directory, which did not exist
    # before this run, so this is positive evidence and not a stale artifact.
    assert_that((config_dir / ".mypy_cache").is_dir()).described_as(
        "mypy wrote no cache at the configured anchor",
    ).is_true()
    # And nothing landed beside the working directory.
    assert_that((workdir / ".mypy_cache").exists()).described_as(
        "mypy scattered a cache into its working directory",
    ).is_false()
