"""Unit tests for competitor detection and runner assembly.

These tests never execute a benchmark; they only build command definitions and
exercise the availability-driven filtering that lets the harness degrade
gracefully when competitors are missing.
"""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from benchmarks.harness import detect, runners
from benchmarks.harness.detect import Availability, CompetitorTool, detect_runners
from benchmarks.harness.runners import SCENARIO_WARMUP, Scenario, build_runners


def test_detect_runners_always_includes_lintro_and_sequential() -> None:
    """Lintro and sequential are always reported as available."""
    availability = detect_runners()

    assert_that(availability[CompetitorTool.LINTRO].available).is_true()
    assert_that(availability[CompetitorTool.SEQUENTIAL].available).is_true()
    assert_that(availability).contains_key(
        CompetitorTool.PRE_COMMIT,
        CompetitorTool.MEGALINTER,
    )


def test_scenario_warmup_cold_is_zero_warm_is_positive() -> None:
    """Cold scenarios have no warmup; warm scenarios discard priming runs."""
    assert_that(SCENARIO_WARMUP[Scenario.FULL_CHECK_COLD]).is_equal_to(0)
    assert_that(SCENARIO_WARMUP[Scenario.FULL_CHECK_WARM]).is_greater_than(0)


def test_build_runners_always_includes_lintro(tmp_path: Path) -> None:
    """Even when include omits lintro, lintro is added as the baseline."""
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "mod.py").write_text("x = 1\n", encoding="utf-8")

    built = build_runners(
        fixture,
        config_dir=tmp_path / "configs",
        include=[CompetitorTool.SEQUENTIAL],
    )

    tools = {runner.tool for runner in built}
    assert_that(tools).contains(CompetitorTool.LINTRO)


def test_build_runners_skips_unavailable_competitor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """An unavailable competitor is filtered out of the runner list."""

    def _fake_detect() -> dict[CompetitorTool, Availability]:
        return {
            CompetitorTool.LINTRO: Availability(
                CompetitorTool.LINTRO,
                True,
                "ok",
            ),
            CompetitorTool.SEQUENTIAL: Availability(
                CompetitorTool.SEQUENTIAL,
                True,
                "ok",
            ),
            CompetitorTool.PRE_COMMIT: Availability(
                CompetitorTool.PRE_COMMIT,
                False,
                "missing",
            ),
            CompetitorTool.MEGALINTER: Availability(
                CompetitorTool.MEGALINTER,
                False,
                "missing",
            ),
        }

    monkeypatch.setattr(runners, "detect_runners", _fake_detect)
    fixture = tmp_path / "fixture"
    fixture.mkdir()

    built = build_runners(fixture, config_dir=tmp_path / "configs")

    tools = {runner.tool for runner in built}
    assert_that(tools).contains(CompetitorTool.LINTRO, CompetitorTool.SEQUENTIAL)
    assert_that(tools).does_not_contain(
        CompetitorTool.PRE_COMMIT,
        CompetitorTool.MEGALINTER,
    )


def test_lintro_runner_command_targets_fixture(tmp_path: Path) -> None:
    """The lintro runner invokes 'lintro chk' against the fixture path."""
    fixture = tmp_path / "fixture"
    fixture.mkdir()

    built = build_runners(
        fixture,
        config_dir=tmp_path / "configs",
        include=[CompetitorTool.LINTRO],
    )
    lintro_runner = next(r for r in built if r.tool == CompetitorTool.LINTRO)

    assert_that(lintro_runner.command).contains("lintro", "chk", str(fixture))


def test_megalinter_uses_docker_when_wrapper_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Without the npm wrapper, MegaLinter is driven via the pinned docker image."""

    def _fake_which(executable: str) -> str | None:
        return None if executable == "mega-linter-runner" else "/usr/bin/docker"

    def _fake_detect() -> dict[CompetitorTool, Availability]:
        base = detect_runners()
        base[CompetitorTool.MEGALINTER] = Availability(
            CompetitorTool.MEGALINTER,
            True,
            "docker",
        )
        return base

    monkeypatch.setattr(runners, "which", _fake_which)
    monkeypatch.setattr(runners, "detect_runners", _fake_detect)
    fixture = tmp_path / "fixture"
    fixture.mkdir()

    built = build_runners(
        fixture,
        config_dir=tmp_path / "configs",
        include=[CompetitorTool.MEGALINTER],
    )
    megalinter = next(r for r in built if r.tool == CompetitorTool.MEGALINTER)

    assert_that(megalinter.command[0]).is_equal_to("docker")
    assert_that(megalinter.command).contains(runners.MEGALINTER_IMAGE)


def test_which_resolves_real_executable() -> None:
    """detect.which resolves an executable that exists on PATH."""
    assert_that(detect.which("python3")).is_not_none()
    assert_that(detect.which("definitely-not-a-real-binary-xyz")).is_none()
