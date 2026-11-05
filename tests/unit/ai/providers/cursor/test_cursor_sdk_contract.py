"""Guard: the real ``cursor-sdk`` accepts what lintro passes it (#3253).

Every other Cursor API test fakes the SDK. This module is the single place the
real package is imported, and it is skipped wherever the opt-in ``cursor``
extra is not installed (CI's unit job syncs ``full`` only). It starts no bridge
and makes no network call: it only builds option objects and reads signatures.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.providers.cursor.sdk_transport import (
    _build_options,
    sdk_agent_options,
)

cursor_sdk = pytest.importorskip("cursor_sdk")


def test_real_options_accept_the_fields_lintro_builds(tmp_path: Path) -> None:
    """``AgentOptions`` / ``LocalAgentOptions`` take lintro's field names.

    Args:
        tmp_path: Stand-in working directory.
    """
    kwargs = sdk_agent_options(model="auto", cwd=str(tmp_path), api_key="k")

    options = _build_options(cursor_sdk, kwargs)

    assert_that(options).is_instance_of(cursor_sdk.AgentOptions)
    assert_that(options.local).is_instance_of(cursor_sdk.LocalAgentOptions)
    assert_that(list(options.tools)).is_empty()
    assert_that(options.cloud).is_none()
    assert_that(options.local.setting_sources).is_none()
    assert_that(str(options.local.cwd)).is_equal_to(str(tmp_path))


def test_real_entry_points_take_the_arguments_lintro_passes() -> None:
    """``launch_bridge(workspace=)`` and ``prompt(message, options, client=)``."""
    launch = inspect.signature(cursor_sdk.AsyncClient.launch_bridge).parameters
    prompt = inspect.signature(cursor_sdk.AsyncAgent.prompt).parameters

    assert_that(launch).contains_key("workspace")
    assert_that(list(prompt)[:2]).is_equal_to(["message", "options"])
    assert_that(prompt["client"].kind).is_equal_to(inspect.Parameter.KEYWORD_ONLY)


def test_real_client_exposes_the_owned_bridge_process() -> None:
    """The orphan-bridge finalizer reads ``client._owned_bridge.process``.

    Both names are private to the SDK. ``CursorSdkBridge`` reaches them with
    ``getattr`` so a rename degrades to "no process to kill" rather than a crash;
    this pins the names so that degradation is noticed when the SDK moves.
    """
    client_source = inspect.getsource(cursor_sdk.AsyncClient)
    bridge_module = importlib.import_module("cursor_sdk._async_bridge")
    bridge_source = inspect.getsource(bridge_module.AsyncBridge)

    assert_that(client_source).contains("self._owned_bridge")
    assert_that(bridge_source).contains("self.process = process")


def test_real_error_classes_share_the_mapped_base() -> None:
    """The classes lintro maps all derive from ``CursorAgentError``."""
    for name in ("AuthenticationError", "RateLimitError", "ConfigurationError"):
        assert_that(
            issubclass(getattr(cursor_sdk, name), cursor_sdk.CursorAgentError),
        ).described_as(name).is_true()
