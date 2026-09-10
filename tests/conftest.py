"""Global test configuration for pytest."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - drives `uv build` for the shared distribution fixture; shell=False
import tempfile
import time
import uuid
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from loguru import logger

from lintro.plugins.discovery import discover_all_tools
from lintro.plugins.registry import ToolRegistry
from lintro.utils.path_utils import normalize_file_path_for_display

# Ensure stable docker builds under pytest-xdist by disabling BuildKit, which
# can be flaky with concurrent builds/tags on some local setups.
os.environ.setdefault("DOCKER_BUILDKIT", "0")

# Session-scoped tool discovery runs before function-scoped fixtures, so the
# developer's real user-level global config must be excluded at import time.
os.environ.setdefault("LINTRO_GLOBAL_CONFIG", "off")


def run_git(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command in a temp repo, isolated from developer git config.

    A global ``commit.gpgsign``, ``core.hooksPath`` or ``init.templateDir``
    would otherwise change how these fixtures behave from machine to machine,
    and an exported ``GIT_INDEX_FILE`` would point git at the wrong index.

    Args:
        cmd: Full argv, starting with ``git``.
        cwd: Working directory.

    Returns:
        The completed process.
    """
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    for leaked in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE"):
        env.pop(leaked, None)
    return (
        subprocess.run(  # nosec B603 B607 - fixed git argv in a temp repo; shell=False
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    )


@pytest.fixture(scope="session", autouse=True)
def disable_global_config() -> None:
    """Keep the developer's real user-level global config out of tests.

    Session-scoped so :func:`discover_all_tools` (also session-scoped) never
    reads the real home global file via plugin trust resolution. Tests that
    exercise the global tier explicitly re-enable it by deleting
    ``LINTRO_GLOBAL_CONFIG`` and patching ``Path.home`` to a temp directory.
    """
    os.environ["LINTRO_GLOBAL_CONFIG"] = "off"


@pytest.fixture(scope="session", autouse=True)
def _discover_tools() -> None:
    """Discover and register all tool plugins before tests run.

    This ensures that ToolRegistry.get() works in all tests by loading
    the builtin tool definitions and any external plugins.
    """
    discover_all_tools()


def _loguru_handler_ids() -> set[int]:
    """Return the ids of the loguru handlers installed right now.

    loguru exposes no public way to enumerate its handlers, so this reads the
    private table defensively: an absent attribute yields an empty set rather
    than an error, which degrades the isolation fixture below to a no-op if a
    future loguru release moves it.

    Returns:
        The currently installed handler ids.
    """
    core = getattr(logger, "_core", None)
    return set(getattr(core, "handlers", {}))


@pytest.fixture(autouse=True)
def _isolate_loguru_handlers() -> Generator[None]:
    """Remove any loguru sink a test adds and leaves behind.

    ``logger.add`` mutates a process-global handler table. A test that adds a
    sink bound to ``sys.stderr`` binds pytest's per-test capture buffer, which
    is closed once that test ends — every later record then raises ``I/O
    operation on closed file`` and loguru prints the traceback to the real
    stdout, which corrupted the JSON that CLI tests parse. Dropping the sinks a
    test added keeps the damage inside the test that caused it (#2315).

    Handlers present beforehand are left alone: loguru closes a sink when it is
    removed, so a removed handler cannot be added back.

    Yields:
        None: Removes newly added loguru handlers after the test.
    """
    before = _loguru_handler_ids()
    try:
        yield
    finally:
        for handler_id in _loguru_handler_ids() - before:
            logger.remove(handler_id)


@pytest.fixture(autouse=True)
def _isolate_plugin_registry() -> Generator[None]:
    """Snapshot and restore the global plugin registry around every test.

    :class:`~lintro.plugins.registry.ToolRegistry` keeps its tool classes,
    lazily built instances and origin labels in class-level dictionaries, so a
    test that registers, clears or re-registers a plugin leaks that state into
    whatever runs next. Restoring the three mappings here makes registry
    mutation local to the test performing it, which is what lets the suite run
    under ``-n auto`` and in randomised order (#2315). Tests no longer need
    ad-hoc save/restore blocks of their own.

    Yields:
        None: Restores the registry mappings once the test has finished.
    """
    with ToolRegistry._lock:
        original_tools = dict(ToolRegistry._tools)
        original_instances = dict(ToolRegistry._instances)
        original_origins = dict(ToolRegistry._origins)
    try:
        yield
    finally:
        with ToolRegistry._lock:
            ToolRegistry._tools = original_tools
            ToolRegistry._instances = original_instances
            ToolRegistry._origins = original_origins


def _build_distributions(dist_dir: Path) -> None:
    """Build the wheel and the sdist into ``dist_dir``.

    Args:
        dist_dir: Directory the distributions are written to.
    """
    project_root = Path(__file__).parent.parent

    build_result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["uv", "build", "--out-dir", str(dist_dir)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert_that(build_result.returncode).described_as(
        f"uv build failed:\n{build_result.stdout}\n{build_result.stderr}",
    ).is_equal_to(0)


# Kept below the 600s per-test budget the tests using this fixture declare:
# fixture setup counts toward pytest-timeout, so a lock wait longer than the
# test's own allowance would be killed by the timeout plugin instead of raising
# the diagnosable error below.
_BUILD_LOCK_TIMEOUT_SECONDS = 300.0
_BUILD_POLL_SECONDS = 0.5


def _read_lock_token(*, lock: Path) -> str | None:
    """Read the ownership token a build lock carries.

    Args:
        lock: Path to the lock file.

    Returns:
        The raw token, or ``None`` when the file is gone or unreadable.
    """
    try:
        return lock.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def _owner_is_gone(*, token: str) -> bool:
    """Report whether the process that wrote a lock token has died.

    Age is not evidence: a slow but live build can hold the lock past any
    threshold, and unlinking it there would let two ``uv build`` runs share the
    project scratch directories. Only a pid that no longer exists proves the
    owner cannot come back and finish.

    Args:
        token: Ownership token read from a lock file, ``"<pid>:<uuid>"``.

    Returns:
        True when the recorded owner is provably dead.
    """
    pid_text, _, _uuid = token.partition(":")
    if not pid_text.isdigit():
        # Not a token this code wrote: never treat it as reclaimable.
        return False
    pid = int(pid_text)
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # A pid we may not signal is still a running process.
        return False
    return False


def _reclaim_stale_lock(*, lock: Path) -> bool:
    """Remove a build lock whose recorded owner is gone.

    The liveness check and the unlink are two separate steps, so a successor
    can take the lock in between. Comparing the token again immediately before
    unlinking — the same compare the release path uses — keeps this from
    deleting that successor's lock and letting two ``uv build`` runs overlap.

    Args:
        lock: Path to the lock file.

    Returns:
        True when this call removed the lock, False when it left it alone.
    """
    token = _read_lock_token(lock=lock)
    if token is None or not _owner_is_gone(token=token):
        return False
    # Re-read rather than trusting the token from before the liveness check.
    if _read_lock_token(lock=lock) != token:
        return False
    try:
        lock.unlink()
    except (FileNotFoundError, OSError):
        return False
    return True


def _release_lock(*, lock: Path, token: str) -> bool:
    """Remove a build lock, but only while this worker still owns it.

    Args:
        lock: Path to the lock file.
        token: Token this worker wrote when it acquired the lock.

    Returns:
        True when this call removed the lock, False when the file had already
        been reclaimed by somebody else.
    """
    if _read_lock_token(lock=lock) != token:
        return False
    try:
        lock.unlink()
    except (FileNotFoundError, OSError):
        return False
    return True


@pytest.fixture(scope="session")
def built_distributions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel and the sdist once for the whole session.

    ``uv build`` keeps its scratch state inside the project directory —
    ``build/``, ``lintro.egg-info/`` and the sdist staging tree — so two builds
    running at once corrupt each other. Under ``pytest -n auto`` that produced
    both outright build failures ("could not delete ...: No such file or
    directory") and, worse, wheels silently missing packages. One build behind
    a lock file, shared by every xdist worker, makes the artifacts
    deterministic and cuts the suite from five builds to one.

    Lives in the root conftest rather than the integration suite because
    ``tests/unit/ai/prompts/test_prompt_loader.py`` also needs a built
    wheel, and an unsynchronised ``uv build`` there raced this one under
    ``-n auto`` (#2315).

    Args:
        tmp_path_factory: Session-scoped temporary directory factory.

    Returns:
        Directory holding the built wheel and source distribution.

    Raises:
        TimeoutError: If another worker holds the build lock for too long.
    """
    # Under xdist each worker gets ``.../pytest-<n>/popen-gw<k>`` and the
    # session root they share is its parent. Without xdist ``getbasetemp()``
    # already is that root, and its own parent persists across sessions, which
    # would hand a later run a stale wheel.
    basetemp = tmp_path_factory.getbasetemp()
    shared_root = basetemp.parent if os.environ.get("PYTEST_XDIST_WORKER") else basetemp
    dist_dir = shared_root / "lintro-dist"
    marker = shared_root / "lintro-dist.complete"
    lock = shared_root / "lintro-dist.lock"

    # Ownership token: the pid proves whether the holder is still alive, and
    # the uuid makes the token unique to this acquisition, so a worker never
    # unlinks a replacement lock that somebody else acquired after its own was
    # reclaimed.
    token = f"{os.getpid()}:{uuid.uuid4().hex}"

    deadline = time.monotonic() + _BUILD_LOCK_TIMEOUT_SECONDS
    while not marker.exists():
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            # A worker that died before its ``finally`` leaves the lock behind
            # forever. Reclaim it only when its recorded owner is gone.
            if _reclaim_stale_lock(lock=lock):
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Timed out waiting for the shared build lock at {lock}",
                ) from exc
            time.sleep(_BUILD_POLL_SECONDS)
            continue
        try:
            os.write(handle, token.encode("utf-8"))
            # Another worker may have finished between the marker check and
            # the lock acquisition.
            if marker.exists():
                break
            # A previous attempt can have died mid-build and left a partial
            # artifact behind; the glob below would happily install it.
            shutil.rmtree(dist_dir, ignore_errors=True)
            dist_dir.mkdir(parents=True)
            _build_distributions(dist_dir=dist_dir)
            marker.touch()
        finally:
            os.close(handle)
            # Release only the lock this worker still owns: if it was reclaimed
            # as stale, the file now belongs to whoever took over.
            _release_lock(lock=lock, token=token)

    # Every caller takes the first match, so more than one artifact of a kind
    # would make which distribution is under test a coin toss.
    assert_that(sorted(dist_dir.glob("*.whl"))).described_as(
        "expected exactly one built wheel",
    ).is_length(1)
    assert_that(sorted(dist_dir.glob("*.tar.gz"))).described_as(
        "expected exactly one built sdist",
    ).is_length(1)

    return dist_dir


@pytest.fixture(scope="session")
def generated_version_artifacts(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Render the version artifacts from repo sources into a session tmp dir.

    Tests that need rendered ``version`` values read the generator's output
    directly instead of the checkout's committed copies, so they keep working
    once the artifacts stop being committed (#2179, epic #2176).

    Args:
        tmp_path_factory: Pytest session-scoped temp directory factory.

    Returns:
        Directory containing ``manifest.json`` and ``_generated_versions.py``.
    """
    from dataclasses import replace

    from lintro_build.versions.generate import main as generate_versions
    from lintro_build.versions.paths import GeneratorPaths

    repo_root = Path(__file__).resolve().parents[1]
    out_dir = tmp_path_factory.mktemp("generated-version-artifacts")
    paths = replace(
        GeneratorPaths.from_repo_root(repo_root),
        manifest_path=out_dir / "manifest.json",
        generated_path=out_dir / "_generated_versions.py",
    )
    rc = generate_versions([], paths=paths)
    if rc != 0:
        pytest.fail(f"version-artifact generation failed with exit code {rc}")
    return out_dir


"""Shared fixtures used across tests in this repository."""


@pytest.fixture
def cli_runner() -> CliRunner:
    """Provide a Click CLI runner for testing.

    Returns:
        CliRunner: CLI runner for invoking commands.
    """
    return CliRunner()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory for testing.

    Yields:
        Path: Path to the temporary directory.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def ruff_violation_file(temp_dir: Path) -> str:
    """Copy the ruff_violations.py sample to a temp directory.

    return normalized path.

    Args:
        temp_dir: Temporary directory fixture.

    Returns:
        str: Normalized path to the copied ruff_violations.py file.
    """
    src = Path("test_samples/tools/python/ruff/ruff_e501_f401_violations.py").resolve()
    dst = temp_dir / "ruff_violations.py"
    shutil.copy(src, dst)
    result: str = normalize_file_path_for_display(str(dst))
    return result


@pytest.fixture
def skip_config_injection(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Skip Lintro config injection for tests.

    Sets LINTRO_SKIP_CONFIG_INJECTION environment variable to disable
    config injection during tests that need to test native tool configs.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        None: This fixture is used for its side effect only.
    """
    monkeypatch.setenv("LINTRO_SKIP_CONFIG_INJECTION", "1")
    yield


#: The checkout's own project config: the only config the singleton may keep
#: between tests. Anything else was resolved from a temporary directory.
_REPO_CONFIG_PATH = str(
    (Path(__file__).parent.parent / ".lintro-config.yaml").resolve(),
)


@pytest.fixture(autouse=True)
def evict_foreign_config_singleton() -> Iterator[None]:
    """Keep a temp directory's config out of every later test (#2375).

    ``config_loader.get_config`` memoises whatever config it resolved from the
    process working directory in a module-level singleton, and modules that
    bind ``get_config`` at import time reach it directly, past any
    monkeypatch. Any test that runs real code from a temporary directory
    therefore leaves the whole worker holding that directory's config: a
    throwaway project has no ``.lintro-config.yaml``, so ``ai.provider``
    disappears and later ``lintro review`` tests fail with "provider unset"
    depending on the shuffle order.

    Only a foreign config is evicted. Keeping the checkout's own config cached
    is what every test already expects, and reloading it after each test cost
    the unit suite well over ten percent.

    Yields:
        None: The check runs after the test body.
    """
    yield

    from lintro.config import config_loader

    loaded = config_loader._loaded_config
    if loaded is not None and loaded.config_path != _REPO_CONFIG_PATH:
        config_loader.clear_config_cache()


@pytest.fixture(autouse=True)
def clear_logging_handlers() -> Iterator[None]:
    """Clear logging handlers before each test.

    Yields:
        None: This fixture is used for its side effect only.
    """
    import logging

    logging.getLogger().handlers.clear()
    yield


_AI_OVERRIDE_ENV_VARS: tuple[str, ...] = (
    "LINTRO_AI_PROVIDER",
    "LINTRO_AI_MODEL",
    "LINTRO_AI_TRANSPORT",
    "LINTRO_AI_ENABLED",
    "LINTRO_AI_REVIEW",
    "LINTRO_AI_MAX_COST_USD",
)


@pytest.fixture(autouse=True)
def clear_ai_config_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep developer ``LINTRO_AI_*`` overrides out of config-resolution tests.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    for name in _AI_OVERRIDE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_local_node_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make project-local Node resolution find nothing.

    Every Node.js tool resolves ``node_modules/.bin`` before ``PATH`` and before
    any ``bunx``/``npx`` fallback (#1811). That first branch is a real
    filesystem walk from the execution directory, so a test that only stubs
    ``shutil.which`` would resolve lintro's own ``node_modules`` whenever the
    checkout happens to have dependencies installed. Use this fixture to
    exercise the later branches deterministically.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.tools.core.command_builders.find_local_node_binary",
        lambda *_args, **_kwargs: None,
    )
