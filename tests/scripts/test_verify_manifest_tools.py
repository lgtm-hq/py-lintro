"""Tests for scripts/ci/verify-manifest-tools.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404 - only TimeoutExpired is used, no process is spawned
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that


def _load_verify_manifest_tools_module() -> ModuleType:
    """Load verify-manifest-tools.py as a module for unit testing."""
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "ci"
        / "verify-manifest-tools.py"
    )
    spec = importlib.util.spec_from_file_location(
        "verify_manifest_tools",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load verify-manifest-tools.py module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_command_returns_manifest_version_command() -> None:
    """verify-manifest-tools should return the entry's version_command verbatim."""
    module = _load_verify_manifest_tools_module()

    # Access private function for testing - module loaded dynamically via importlib
    tool_command_fn = module._tool_command
    cmd = tool_command_fn(
        "astro_check",
        {
            "name": "astro_check",
            "install": {"type": "npm", "package": "astro", "bin": "astro"},
            "version_command": ["astro", "--version"],
        },
    )

    assert_that(cmd).is_equal_to(["astro", "--version"])


def test_tool_command_rejects_missing_version_command() -> None:
    """verify-manifest-tools should raise when version_command is absent."""
    module = _load_verify_manifest_tools_module()

    tool_command_fn = module._tool_command
    assert_that(tool_command_fn).raises(ValueError).when_called_with(
        "astro_check",
        {"name": "astro_check", "install": {"type": "npm"}},
    )


def test_clippy_versions_match_ignores_unobservable_patch() -> None:
    """Clippy matches at major.minor since its binary never reports a patch.

    `cargo clippy --version` emits `clippy 0.1.<minor>`, which the parser maps
    to `1.<minor>.0`. A manifest that pins a real toolchain patch (e.g. 1.97.1)
    must still match that synthesized `.0`.
    """
    module = _load_verify_manifest_tools_module()

    versions_match = module._versions_match
    assert_that(versions_match("clippy", "1.97.1", "1.97.0")).is_true()
    assert_that(versions_match("clippy", "1.97.0", "1.97.0")).is_true()


def test_clippy_versions_mismatch_on_minor_drift() -> None:
    """Clippy still fails when the observable major.minor genuinely drifts."""
    module = _load_verify_manifest_tools_module()

    versions_match = module._versions_match
    assert_that(versions_match("clippy", "1.97.1", "1.96.0")).is_false()


def test_non_clippy_versions_require_exact_match() -> None:
    """Non-clippy tools keep strict, patch-level version equality."""
    module = _load_verify_manifest_tools_module()

    versions_match = module._versions_match
    assert_that(versions_match("ruff", "1.97.1", "1.97.1")).is_true()
    assert_that(versions_match("ruff", "1.97.1", "1.97.0")).is_false()


def test_version_mismatch_names_the_lagging_image_and_digest_bump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A main failure points directly at the missing candidate digest pin."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="git",
        version="99.0.0",
        version_command=["git", "--version"],
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd: (0, "git version 1.2.3", False),
    )
    monkeypatch.setenv(
        "LINTRO_IMAGE_REF",
        "ghcr.io/lgtm-hq/py-lintro:ci-123@sha256:" + "a" * 64,
    )

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(1)
    output = capsys.readouterr().out
    assert_that(output).contains("digest-bump required")
    assert_that(output).contains("git")
    assert_that(output).contains("99.0.0")
    assert_that(output).contains("ci-123@sha256:")


def test_version_mismatch_names_manifest_bump_for_newer_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A newer image directs maintainers to bump the manifest."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="git",
        version="1.0.0",
        version_command=["git", "--version"],
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd: (0, "git version 2.0.0", False),
    )

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(1)
    output = capsys.readouterr().out
    assert_that(output).contains("manifest bump required")
    assert_that(output).contains("newer than the manifest")
    assert_that(output).does_not_contain("digest-bump required")


def test_version_mismatch_reports_unavailable_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unparseable version ordering produces an actionable diagnostic."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="git",
        version="latest",
        version_command=["git", "--version"],
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd: (0, "git version 1.2.3", False),
    )

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(1)
    assert_that(capsys.readouterr().out).contains("version ordering unavailable")


def test_parse_allow_missing_splits_and_dedupes() -> None:
    """--allow-missing values are comma-split, trimmed, and de-duplicated."""
    module = _load_verify_manifest_tools_module()

    parse = module._parse_allow_missing
    assert_that(parse(None)).is_equal_to(set())
    assert_that(parse([])).is_equal_to(set())
    assert_that(parse(["terraform"])).is_equal_to({"terraform"})
    assert_that(parse(["a, b ", "b,c", " "])).is_equal_to({"a", "b", "c"})


def _write_manifest(
    tmp_path: Path,
    *,
    name: str,
    version: str,
    version_command: list[str],
) -> Path:
    """Write a single-tool manifest to a temp file.

    Args:
        tmp_path: Pytest temporary directory.
        name: Tool name.
        version: Manifest-declared version.
        version_command: Command used to probe the installed version.

    Returns:
        Path: The written manifest file.
    """
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "tools": [
                    {
                        "name": name,
                        "version": version,
                        "tier": "tools",
                        "version_command": version_command,
                    },
                ],
            },
        ),
    )
    return manifest


def _run_main(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> int:
    """Invoke the verifier's main() with a synthetic argv.

    Args:
        module: The loaded verify-manifest-tools module.
        monkeypatch: Pytest monkeypatch fixture.
        argv: Arguments following the program name.

    Returns:
        int: The main() exit code.
    """
    monkeypatch.setattr("sys.argv", ["verify-manifest-tools.py", *argv])
    # module is loaded dynamically via importlib, so main() is typed as Any.
    return int(module.main())


def test_allow_missing_tool_absent_passes_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An allow-missing tool whose binary is absent passes with a loud warning."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="brandnew",
        version="1.0.0",
        version_command=["definitely-not-a-real-binary-xyz", "--version"],
    )

    code = _run_main(
        module,
        monkeypatch,
        ["--manifest", str(manifest), "--allow-missing", "brandnew"],
    )

    assert_that(code).is_equal_to(0)
    out = capsys.readouterr().out
    assert_that(out).contains("::warning::")
    assert_that(out).contains("brandnew")


def test_allow_missing_tool_present_version_mismatch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allow-missing tool that IS present must still version-match."""
    module = _load_verify_manifest_tools_module()
    # `git --version` is a real, present binary that never reports 99.0.0.
    manifest = _write_manifest(
        tmp_path,
        name="git",
        version="99.0.0",
        version_command=["git", "--version"],
    )

    code = _run_main(
        module,
        monkeypatch,
        ["--manifest", str(manifest), "--allow-missing", "git"],
    )

    assert_that(code).is_equal_to(1)


def test_non_allowed_missing_tool_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing binary for a tool NOT in allow-missing is still a hard failure."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="brandnew",
        version="1.0.0",
        version_command=["definitely-not-a-real-binary-xyz", "--version"],
    )

    code = _run_main(
        module,
        monkeypatch,
        ["--manifest", str(manifest)],
    )

    assert_that(code).is_equal_to(1)


def test_empty_allow_missing_leaves_behavior_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty allowlist keeps full enforcement: a present, matching tool passes."""
    module = _load_verify_manifest_tools_module()
    # `git --version` -> "git version X.Y.Z"; declare that exact version so the
    # match succeeds regardless of the runner's git build.
    _, output, _ = module._run(["git", "--version"])
    actual = module._parse_version(output, "git")
    manifest = _write_manifest(
        tmp_path,
        name="git",
        version=str(actual),
        version_command=["git", "--version"],
    )

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(0)


def test_parse_allow_version_lag_splits_and_dedupes() -> None:
    """--allow-version-lag uses the same comma-split parsing as allow-missing."""
    module = _load_verify_manifest_tools_module()

    parse = module._parse_allow_version_lag
    assert_that(parse(None)).is_equal_to(set())
    assert_that(parse(["astro_check, ruff", "ruff"])).is_equal_to(
        {"astro_check", "ruff"},
    )


def test_is_image_older_than_manifest_ordering() -> None:
    """Numeric segment ordering distinguishes older / equal / newer images."""
    module = _load_verify_manifest_tools_module()

    older = module._is_image_older_than_manifest
    assert_that(older(expected="7.1.3", actual="7.0.9")).is_true()
    assert_that(older(expected="7.1.3", actual="7.1.3")).is_false()
    assert_that(older(expected="7.1.0", actual="7.1.3")).is_false()
    assert_that(older(expected="7.1", actual="7.0.9")).is_true()


def test_version_tuple_stops_at_prerelease_tag() -> None:
    """A pre-release tag stops parsing so "7.1.0-rc.1" is (7, 1, 0)."""
    module = _load_verify_manifest_tools_module()

    version_tuple = module._version_tuple
    assert_that(version_tuple("7.1.0-rc.1")).is_equal_to((7, 1, 0))
    assert_that(version_tuple("7.1.3")).is_equal_to((7, 1, 3))
    # A pre-release build must not read as newer than its release.
    older = module._is_image_older_than_manifest
    assert_that(older(expected="7.1.0", actual="7.1.0-rc.1")).is_false()


def test_allow_version_lag_older_image_passes_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An allow-version-lag tool with an older installed version warns, not fails."""
    module = _load_verify_manifest_tools_module()
    _, output, _ = module._run(["git", "--version"])
    actual = module._parse_version(output, "git")
    assert_that(actual).is_not_none()
    # Declare a version strictly newer than whatever git reports on this runner.
    parts = [int(p) for p in str(actual).split(".")]
    parts[0] += 1
    expected = ".".join(str(p) for p in parts)
    manifest = _write_manifest(
        tmp_path,
        name="git",
        version=expected,
        version_command=["git", "--version"],
    )

    code = _run_main(
        module,
        monkeypatch,
        ["--manifest", str(manifest), "--allow-version-lag", "git"],
    )

    assert_that(code).is_equal_to(0)
    out = capsys.readouterr().out
    assert_that(out).contains("::warning::")
    assert_that(out).contains("version lag")
    assert_that(out).contains("git")


def test_allow_version_lag_newer_image_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image-newer-than-manifest stays a hard failure even with version-lag."""
    module = _load_verify_manifest_tools_module()
    # Manifest declares an impossibly old version so the real git binary is newer.
    manifest = _write_manifest(
        tmp_path,
        name="git",
        version="0.0.1",
        version_command=["git", "--version"],
    )

    code = _run_main(
        module,
        monkeypatch,
        ["--manifest", str(manifest), "--allow-version-lag", "git"],
    )

    assert_that(code).is_equal_to(1)


def test_allow_version_lag_missing_binary_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Version-lag does not tolerate a missing binary (unlike allow-missing)."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="brandnew",
        version="1.0.0",
        version_command=["definitely-not-a-real-binary-xyz", "--version"],
    )

    code = _run_main(
        module,
        monkeypatch,
        ["--manifest", str(manifest), "--allow-version-lag", "brandnew"],
    )

    assert_that(code).is_equal_to(1)


def _fake_timeout_run(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: bytes | str | None,
) -> None:
    """Make every probe raise TimeoutExpired with the given captured output.

    Args:
        module: The loaded verify-manifest-tools module.
        monkeypatch: Pytest monkeypatch fixture.
        stdout: Output captured before the timeout fired, as bytes, text, or
            ``None`` when the command produced nothing.
    """

    def _raise(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["semgrep", "--version"],
            timeout=10,
            output=stdout,
        )

    monkeypatch.setattr(module.subprocess, "run", _raise)


def test_run_returns_timeout_code_with_captured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out probe reports code 124 and the output captured so far."""
    module = _load_verify_manifest_tools_module()
    _fake_timeout_run(module, monkeypatch, stdout=b"1.151.0\n")

    code, output, timed_out = module._run(["semgrep", "--version"])

    assert_that(code).is_equal_to(124)
    assert_that(output).is_equal_to("1.151.0")
    assert_that(timed_out).is_true()


def test_timeout_with_matching_version_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hung probe that already printed the expected version passes (#1874)."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="semgrep",
        version="1.151.0",
        version_command=["semgrep", "--version"],
    )
    _fake_timeout_run(module, monkeypatch, stdout=b"1.151.0\n")

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(0)
    out = capsys.readouterr().out
    assert_that(out).contains("::notice::")
    assert_that(out).contains("semgrep")


def test_timeout_without_output_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A timeout with no usable version output stays a hard failure."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="semgrep",
        version="1.151.0",
        version_command=["semgrep", "--version"],
    )
    _fake_timeout_run(module, monkeypatch, stdout=None)

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(1)
    out = capsys.readouterr().out
    assert_that(out).contains("exit code 124")


def test_ordinary_exit_code_124_is_not_treated_as_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child process that legitimately returns 124 is NOT a timeout.

    `_TIMEOUT_EXIT` (124) is only synthetic when `_run` actually caught
    `subprocess.TimeoutExpired`. A well-behaved probe that happens to exit
    with the same numeric code -- while printing output that matches the
    manifest version -- must still hard-fail; only a genuine timeout may use
    the exit-lag tolerance.
    """
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="semgrep",
        version="1.151.0",
        version_command=["semgrep", "--version"],
    )

    class _FakeCompleted:
        returncode = 124
        stdout = "1.151.0\n"
        stderr = ""

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: _FakeCompleted(),
    )

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(1)


def test_timeout_with_wrong_version_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung probe whose printed version drifts is not tolerated."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="semgrep",
        version="1.151.0",
        version_command=["semgrep", "--version"],
    )
    _fake_timeout_run(module, monkeypatch, stdout=b"1.150.0\n")

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(1)


def test_numerically_equal_versions_ask_for_a_manifest_string_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``7.1`` vs ``7.1.0`` is a spelling mismatch, not an unorderable pair."""
    module = _load_verify_manifest_tools_module()
    manifest = _write_manifest(
        tmp_path,
        name="git",
        version="7.1",
        version_command=["git", "--version"],
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd: (0, "git version 7.1.0", False),
    )

    code = _run_main(module, monkeypatch, ["--manifest", str(manifest)])

    assert_that(code).is_equal_to(1)
    output = capsys.readouterr().out
    assert_that(output).contains("align the manifest string to the installed version")
    assert_that(output).does_not_contain("version ordering unavailable")
