"""The no-event-loop fallback that ends an orphaned Cursor SDK bridge (#3253).

A provider that is never closed (``orchestrator.py`` does not call
``aclose``) would leave the bridge subprocess running once its loop is gone.
:func:`_kill_bridge` is what the weakref finalizer runs then; these tests point
it at a real child process.
"""

from __future__ import annotations

import subprocess  # nosec B404 - spawns a sleeping Python child to signal; shell=False
import sys
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from assertpy import assert_that

from lintro.ai.providers.cursor.sdk_transport import _kill_bridge


@pytest.fixture()
def sleeper() -> Iterator[subprocess.Popen[bytes]]:
    """Start a child that sleeps until signalled, and reap it afterwards.

    Yields:
        subprocess.Popen[bytes]: The running child.
    """
    child = subprocess.Popen(  # nosec B603 - fixed argv, no shell
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    try:
        yield child
    finally:
        child.kill()
        child.wait(timeout=10)


def test_a_running_bridge_is_terminated(
    sleeper: subprocess.Popen[bytes],
) -> None:
    """A bridge whose process has not exited is sent SIGTERM.

    Args:
        sleeper: The stand-in bridge process.
    """
    _kill_bridge(SimpleNamespace(pid=sleeper.pid, returncode=None))

    assert_that(sleeper.wait(timeout=10)).is_not_equal_to(0)


def test_an_exited_bridge_is_not_signalled(
    sleeper: subprocess.Popen[bytes],
) -> None:
    """A recorded exit code means the pid may be reused; leave it alone.

    Args:
        sleeper: A live process standing in for a recycled pid.
    """
    _kill_bridge(SimpleNamespace(pid=sleeper.pid, returncode=0))

    assert_that(sleeper.poll()).is_none()
