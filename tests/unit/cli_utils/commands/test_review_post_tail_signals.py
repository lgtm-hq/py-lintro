"""The ``lintro review`` JSON envelope survives SIGTERM and post failures (#3426).

The JSON is written after posting so it can report delivery. The CI wrapper
classifies the round from it, and SIGTERM can arrive more than once (the
wrapper and ``uv run`` both forward it), so neither a repeated signal nor
one during rendering may leave a missing or truncated envelope. Each case
runs in a child process so a signal can never reach the pytest worker.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess  # nosec B404 - runs this repo's child script with a fixed argv
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX signal semantics",
)

_CHILD = Path(__file__).with_name("review_post_tail_child.py")
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _start(*, scenario: str, marker: Path) -> subprocess.Popen[str]:
    """Start the child for *scenario*.

    Args:
        scenario: Child scenario name.
        marker: File the child writes its stage to.

    Returns:
        The running child process.
    """
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.Popen(  # nosec B603 - fixed argv, shell=False
        [sys.executable, str(_CHILD), scenario, str(marker)],
        cwd=_REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_stage(
    *,
    child: subprocess.Popen[str],
    marker: Path,
    stage: str,
) -> None:
    """Block until the child reports *stage*.

    Args:
        child: The running child.
        marker: File the child writes its stage to.
        stage: Stage to wait for.
    """
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and child.poll() is None:
        if marker.exists() and marker.read_text(encoding="utf-8") == stage:
            return
        time.sleep(0.02)
    child.kill()
    _, stderr = child.communicate()
    pytest.fail(f"child never reached {stage!r}:\n{stderr}")


def _finish(*, child: subprocess.Popen[str]) -> tuple[int, dict[str, Any]]:
    """Collect the child's exit code and parse its stdout as the envelope.

    Args:
        child: The running child.

    Returns:
        The exit code and the parsed JSON document.
    """
    stdout, stderr = child.communicate(timeout=60)
    try:
        document: dict[str, Any] = json.loads(stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"stdout is not complete JSON ({len(stdout)} chars):\n{stderr[-2000:]}",
        )
    return child.returncode, document


def _delivered(*, document: dict[str, Any]) -> dict[str, bool | None]:
    """Map finding titles to ``inline_delivered``.

    Args:
        document: The parsed envelope.

    Returns:
        Title to delivery flag.
    """
    return {f["title"]: f["inline_delivered"] for f in document["findings"]}


@pytest.mark.parametrize(
    ("scenario", "stages", "thread_delivered"),
    [
        pytest.param(
            "post-sigterm",
            ("posting", "rendering"),
            False,
            id="two-sigterms-mid-post",
        ),
        pytest.param(
            "render-sigterm",
            ("rendering", "rendering"),
            True,
            id="sigterm-mid-render",
        ),
    ],
)
def test_sigterm_waits_for_the_complete_envelope(
    tmp_path: Path,
    scenario: str,
    stages: tuple[str, str],
    thread_delivered: bool,
) -> None:
    """Two SIGTERMs exit 143 only after the whole JSON is written.

    Mid-post, the first signal stops the post and the second lands while the
    output renders; it must be ignored, not kill the process. The post was
    attempted and not confirmed. Mid-render, after a successful post, both
    are held off and the delivered thread is reported.

    Args:
        tmp_path: Pytest temporary directory.
        scenario: Child scenario name.
        stages: Stage at which the parent sends each SIGTERM.
        thread_delivered: Delivery the eligible finding must report.
    """
    marker = tmp_path / "stage"
    child = _start(scenario=scenario, marker=marker)

    for stage in stages:
        _wait_for_stage(child=child, marker=marker, stage=stage)
        child.send_signal(signal.SIGTERM)
    code, document = _finish(child=child)

    assert_that(code).is_equal_to(128 + signal.SIGTERM)
    assert_that(_delivered(document=document)).is_equal_to(
        {"Thread": thread_delivered, "Note": False},
    )
    assert_that([f["posted_inline"] for f in document["findings"]]).is_equal_to(
        [True, False],
    )


def test_a_post_that_raises_still_writes_the_envelope_and_exits_1(
    tmp_path: Path,
) -> None:
    """A raw ``TimeoutError`` from posting exits 1 with the JSON written.

    ``api_response`` does not catch a timeout from ``getresponse()``, so it
    escapes the poster. As before posting moved ahead of the output, the run
    fails with exit 1, and the envelope is still complete.

    Args:
        tmp_path: Pytest temporary directory.
    """
    child = _start(scenario="post-timeout", marker=tmp_path / "stage")

    code, document = _finish(child=child)

    assert_that(code).is_equal_to(1)
    assert_that(_delivered(document=document)).is_equal_to(
        {"Thread": False, "Note": False},
    )
