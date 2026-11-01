"""Observable outcome construction and adaptation for legacy consumers."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, asdict, fields, replace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.enums.verify_status import VerifyStatus
from lintro.models.core.tool_outcome import (
    Checked,
    FixMeasured,
    FixPartial,
    FixUnmeasured,
    NoFiles,
    Skipped,
    TimedOut,
    ToolOutcome,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools.core.verify_pass import (
    VerifyOutcome,
    VerifyScope,
    fold_verify_results,
)
from lintro.utils.execution.exit_codes import (
    aggregate_tool_results,
    determine_exit_code,
)
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.result_formatters import print_tool_result
from lintro.utils.summary_tables import print_summary_table


@pytest.mark.parametrize("action", [Action.CHECK, Action.FIX])
@pytest.mark.parametrize(
    ("legacy", "execution"),
    [
        ({"success": True, "no_files": True, "inspected_files": False}, NoFiles()),
        (
            {
                "skipped": True,
                "success": True,
                "inspected_files": False,
                "skip_reason": "missing configuration",
            },
            Skipped("missing configuration"),
        ),
        ({"timed_out": True, "inspected_files": False}, TimedOut(None)),
    ],
)
def test_inactive_producers_preserve_legacy_console_rendering(
    legacy: dict[str, Any],
    execution: NoFiles | Skipped | TimedOut,
    action: Action,
) -> None:
    """Explicit inactive evidence preserves the same console rendering."""
    original = ToolResult(name="clippy", **legacy)
    migrated = ToolResult.from_outcome(ToolOutcome(execution), name="clippy")

    def render(result: ToolResult) -> list[dict[str, Any]]:
        """Capture complete tool and summary output, including color arguments."""
        events: list[dict[str, Any]] = []

        def emit(**payload: Any) -> None:
            events.append(payload)

        print_tool_result(
            console_output_func=emit,
            success_func=emit,
            tool_name=result.name,
            output=result.output or "",
            issues_count=result.issues_count,
            action=action,
            success=result.success,
            result=result,
        )
        print_summary_table(emit, action, [result])
        return events

    assert_that(original.inspected_files).is_false()
    assert_that(asdict(migrated)).is_equal_to(asdict(original))
    output = render(migrated)
    assert_that(output).is_equal_to(render(original))
    text = "\n".join(str(event.get("text", "")) for event in output)
    if isinstance(execution, NoFiles):
        assert_that(text).contains(
            "No files processed (excluded by patterns)",
            "no files matched",
        )
    else:
        assert_that(text).does_not_contain("No files processed", "no files matched")
        assert_that(text).contains(
            "missing configuration" if isinstance(execution, Skipped) else "FAIL",
        )


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ({}, ToolOutcome(Checked(False))),
        (
            {"success": True, "inspected_files": True},
            ToolOutcome(Checked(True, inspected_files=True)),
        ),
        (
            {"success": True, "inspected_files": False},
            ToolOutcome(Checked(True, inspected_files=False)),
        ),
        (
            {"issues_count": 2},
            ToolOutcome(Checked(False), issues_count=2),
        ),
        (
            {"success": True, "issues_count": 2},
            ToolOutcome(Checked(True), issues_count=2),
        ),
        (
            {
                "skipped": True,
                "success": True,
                "inspected_files": False,
                "skip_reason": "not installed",
            },
            ToolOutcome(Skipped("not installed")),
        ),
        (
            {"success": True, "no_files": True, "inspected_files": False},
            ToolOutcome(NoFiles()),
        ),
        (
            {
                "success": True,
                "no_files": True,
                "inspected_files": False,
                "initial_issues_count": 0,
                "fixed_issues_count": 0,
                "remaining_issues_count": 0,
            },
            ToolOutcome(NoFiles(), FixMeasured(0, 0, 0)),
        ),
        (
            {"timed_out": True, "inspected_files": False},
            ToolOutcome(TimedOut("raw output")),
        ),
        (
            {"initial_issues_count": 2, "issues_count": 2},
            ToolOutcome(Checked(False), FixPartial(initial=2), issues_count=2),
        ),
        (
            {"fixed_issues_count": 1, "remaining_issues_count": 0},
            ToolOutcome(Checked(False), FixPartial(fixed=1, remaining=0)),
        ),
        (
            {
                "issues_count": 1,
                "initial_issues_count": 2,
                "fixed_issues_count": 1,
                "remaining_issues_count": 1,
            },
            ToolOutcome(Checked(False), FixMeasured(2, 1, 1), issues_count=1),
        ),
        (
            {
                "timed_out": True,
                "inspected_files": False,
                "issues_count": 1,
                "initial_issues_count": 2,
                "fixed_issues_count": 1,
                "remaining_issues_count": 1,
            },
            ToolOutcome(TimedOut("raw output"), FixMeasured(2, 1, 1), issues_count=1),
        ),
        (
            {
                "timed_out": True,
                "inspected_files": False,
                "issues_count": 2,
                "initial_issues_count": 2,
                "residual_unknown": True,
                "residual_unknown_reason": "verify timed out",
            },
            ToolOutcome(
                TimedOut("raw output"),
                FixUnmeasured("verify timed out", initial=2),
                issues_count=2,
            ),
        ),
        (
            {"residual_unknown": True, "residual_unknown_reason": "report incomplete"},
            ToolOutcome(Checked(False), FixUnmeasured("report incomplete")),
        ),
    ],
)
def test_legacy_roundtrip_preserves_payload_and_serialized_bytes(
    legacy: dict[str, Any],
    expected: ToolOutcome,
) -> None:
    """Both directions retain counters, evidence and the whole result envelope.

    Args:
        legacy: State recorded by an existing producer.
        expected: Explicit execution and accounting for those fields.
    """
    issue = BaseIssue(file="a.py", line=1, message="finding")
    original = ToolResult(
        name="plugin",
        output="raw output",
        formatted_output="rendered output",
        issues=[issue] if expected.issues_count else [],
        initial_issues=(issue,) if expected.issues_count else (),
        pytest_summary={"passed": 2},
        metadata={"suppressions": [{"reason": "accepted"}]},
        cwd="/repo",
        parse_failures_count=0,
        duration_seconds=1.25,
        capability=Cap.FIX,
        **legacy,
    )
    before = asdict(original)
    wire = {
        action: json.dumps(serialize_tool_result(original, action=action))
        for action in (Action.CHECK, Action.FIX)
    }

    assert_that(original.outcome).is_equal_to(expected)
    restored = original.with_outcome(original.outcome)
    constructed = ToolResult.from_outcome(
        expected,
        **{
            name: value
            for name, value in vars(original).items()
            if name not in expected.to_legacy_fields()
        },
    )

    assert_that(asdict(restored)).is_equal_to(before)
    assert_that(asdict(constructed)).is_equal_to(before)
    assert_that(asdict(original)).is_equal_to(before)
    assert_that(restored.outcome).is_equal_to(expected)
    assert_that(restored.metadata).is_same_as(original.metadata)
    assert_that(restored.issues).is_same_as(original.issues)
    assert_that(restored.initial_issues).is_same_as(original.initial_issues)
    assert_that([f.name for f in fields(restored)]).does_not_contain("outcome")
    for action, serialized in wire.items():
        assert_that(
            json.dumps(serialize_tool_result(constructed, action=action)),
        ).is_equal_to(serialized)
        assert_that(
            json.dumps(serialize_tool_result(restored, action=action)),
        ).is_equal_to(
            serialized,
        )


@pytest.mark.parametrize(
    "counts",
    [
        (None, None, None),
        (None, None, 0),
        (None, 0, None),
        (None, 0, 0),
        (0, None, None),
        (0, None, 0),
        (0, 0, None),
    ],
)
def test_incomplete_counts_never_become_measured_or_filled(
    counts: tuple[int | None, int | None, int | None],
) -> None:
    """All missing-counter combinations survive even beside empty findings.

    Args:
        counts: An incomplete legacy before/fixed/remaining triple.
    """
    original = ToolResult(
        initial_issues_count=counts[0],
        fixed_issues_count=counts[1],
        remaining_issues_count=counts[2],
    )
    outcome = original.outcome
    restored = ToolResult.from_outcome(outcome)

    assert_that(outcome.accounting).is_equal_to(FixPartial(*counts))
    assert_that(
        (
            restored.initial_issues_count,
            restored.fixed_issues_count,
            restored.remaining_issues_count,
        ),
    ).is_equal_to(counts)
    assert_that(restored.residual_unknown).is_false()
    assert_that(restored.inspected_files).is_none()


@pytest.mark.parametrize(
    ("execution", "success"),
    [
        (NoFiles(), True),
        (Skipped("missing dependencies"), True),
        (TimedOut(None), False),
    ],
)
def test_explicit_inactive_outcomes_derive_verdict_and_no_inspection(
    execution: NoFiles | Skipped | TimedOut,
    success: bool,
) -> None:
    """Non-execution never inherits the legacy constructor's default verdict.

    Args:
        execution: Producer evidence of a non-executing result.
        success: Successful non-event or failed timeout.
    """
    outcome = ToolOutcome(execution)
    result = ToolResult.from_outcome(outcome, name="plugin")

    assert_that(result.outcome).is_equal_to(outcome)
    assert_that(result.success).is_equal_to(success)
    assert_that(result.inspected_files).is_false()
    assert_that(result.initial_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()


@pytest.mark.parametrize(
    "payload",
    [
        {"success": True},
        {"no_files": True},
        {"skipped": True, "skip_reason": "missing"},
        {"timed_out": True},
        {"issues_count": -1},
        {"initial_issues_count": 0},
        {"residual_unknown": True},
        {"inspected_files": True},
    ],
)
def test_outcome_constructor_rejects_competing_legacy_state(
    payload: dict[str, Any],
) -> None:
    """A caller cannot override the explicit outcome through envelope fields.

    Args:
        payload: A legacy state override, even if individually valid.
    """
    outcome = ToolOutcome(Checked(False))
    with pytest.raises(TypeError, match="outcome owns state fields"):
        ToolResult.from_outcome(outcome, **payload)
    assert_that(outcome.success).is_false()


def test_timeout_output_is_owned_by_outcome_at_construction() -> None:
    """Shared branches may repeat output, but cannot silently overwrite it."""
    outcome = ToolOutcome(TimedOut("deadline output"))
    result = ToolResult.from_outcome(outcome, output="deadline output")
    assert_that(result.output).is_equal_to("deadline output")
    assert_that(result.outcome).is_equal_to(outcome)

    with pytest.raises(TypeError, match="timeout output must match"):
        ToolResult.from_outcome(ToolOutcome(TimedOut(None)), output="old output")


@pytest.mark.parametrize(
    "legacy",
    [
        {"skipped": True, "skip_reason": "missing", "inspected_files": True},
        {"no_files": True, "success": True, "inspected_files": True},
        {"timed_out": True, "inspected_files": True},
        {"no_files": True, "inspected_files": False},
        {"timed_out": True, "success": True, "inspected_files": False},
        {
            "no_files": True,
            "success": True,
            "inspected_files": False,
            "issues_count": 1,
        },
        {
            "skipped": True,
            "success": True,
            "inspected_files": False,
            "skip_reason": "missing",
            "initial_issues_count": 1,
        },
        {"skip_reason": ""},
        {"residual_unknown_reason": ""},
        {"issues_count": -1},
    ],
)
def test_legacy_constructor_rejects_contradictory_outcome_evidence(
    legacy: dict[str, Any],
) -> None:
    """Contradictory legacy fields fail at construction before reaching consumers.

    Args:
        legacy: Constructor inputs tolerated before explicit producer migration.
    """
    assert_that(ToolResult).raises(ValueError).when_called_with(**legacy)


def test_unmeasured_adapter_preserves_null_json_and_failure_exit() -> None:
    """Explicit unknown accounting keeps the existing wire bytes and failed run."""
    result = ToolResult(
        name="ruff",
        issues=[BaseIssue(file="a.py", line=1, message="finding")],
    ).with_outcome(
        ToolOutcome(Checked(False), FixUnmeasured("verify failed"), issues_count=1),
    )
    wire = json.dumps(serialize_tool_result(result, action=Action.FIX))

    assert_that(wire).is_equal_to(
        '{"tool": "ruff", "success": false, "issues_count": 1, "skipped": false, '
        '"skip_reason": null, "timed_out": false, "output": null, "status": "failed", '
        '"net_resolved": null, "fixed": null, "remaining": null, '
        '"residual_unknown": true, "residual_unknown_reason": "verify failed", '
        '"issues": [{"file": "a.py", "line": 1, "code": "", "message": "finding"}]}',
    )
    assert_that(result.initial_issues_count).is_none()
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((1, 0, 0))
    assert_that(determine_exit_code(Action.FIX, [result], 1, 0)).is_equal_to(1)


@pytest.mark.parametrize("execution", [Checked(False), TimedOut("deadline")])
def test_measured_zero_residual_does_not_erase_execution_failure(
    execution: Checked | TimedOut,
) -> None:
    """Accounting and execution remain independent even with no survivors.

    Args:
        execution: Failed execution whose later accounting is complete.
    """
    result = ToolResult(name="formatter").with_outcome(
        ToolOutcome(execution, FixMeasured(2, 2, 0)),
    )

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_zero()
    assert_that(result.outcome.execution).is_equal_to(execution)
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((0, 2, 0))
    assert_that(determine_exit_code(Action.FIX, [result], 0, 0)).is_equal_to(1)


def test_adapter_replaces_state_without_retaining_obsolete_flags() -> None:
    """A measured replacement of a timeout clears unknown and timeout state."""
    failed = ToolResult(
        name="formatter",
        timed_out=True,
        inspected_files=False,
    ).with_outcome(
        ToolOutcome(TimedOut("deadline"), FixUnmeasured("no verification")),
    )
    recovered = failed.with_outcome(
        ToolOutcome(Checked(True, inspected_files=True), FixMeasured(2, 2, 0)),
    )

    assert_that(recovered.success).is_true()
    assert_that(recovered.timed_out).is_false()
    assert_that(recovered.residual_unknown).is_false()
    assert_that(recovered.residual_unknown_reason).is_none()
    assert_that(recovered.inspected_files).is_true()
    assert_that(recovered.fixed_issues_count).is_equal_to(2)
    assert_that(recovered.output).is_equal_to("deadline")
    assert_that(failed.timed_out).is_true()
    assert_that(failed.fixed_issues_count).is_none()


def test_verify_fold_rebuilds_validated_state_without_changing_original() -> None:
    """A verify fold replaces the list entry and leaves the original state intact."""
    result = ToolResult(
        name="ruff",
        issues_count=1,
        issues=[BaseIssue(file="/repo/a.py", line=1, message="finding")],
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
        inspected_files=True,
    )
    original = result
    before = result.outcome

    folded_results = [result]
    fold_verify_results(
        mutation_results=folded_results,
        verify_results=[
            VerifyOutcome(
                tool="ruff",
                result=None,
                status=VerifyStatus.UNKNOWN,
                unknown_reason="verify failed",
            ),
        ],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )
    result = folded_results[0]

    assert_that(original.outcome).is_equal_to(before)
    assert_that(result).is_not_same_as(original)
    assert_that(before.accounting).is_equal_to(FixMeasured(1, 0, 1))
    assert_that(result.outcome.accounting).is_equal_to(
        FixUnmeasured("verify failed", 1),
    )
    copied = replace(result, output="new display text")
    assert_that(copied.outcome).is_equal_to(result.outcome)
    assert_that(copied.output).is_equal_to("new display text")
    assert_that(result.with_outcome(result.outcome)).is_equal_to(result)


@pytest.mark.parametrize(
    ("factory", "payload", "error"),
    [
        (Skipped, {"reason": ""}, ValueError),
        (Skipped, {"reason": None}, ValueError),
        (FixUnmeasured, {"reason": "   "}, ValueError),
        (FixUnmeasured, {"reason": None}, ValueError),
        (FixUnmeasured, {"reason": "failed", "fixed": 0}, TypeError),
        (FixUnmeasured, {"reason": "failed", "remaining": 0}, TypeError),
        (FixUnmeasured, {"reason": "failed", "initial": -1}, ValueError),
        (FixMeasured, {"initial": None, "fixed": 0, "remaining": 0}, ValueError),
        (FixMeasured, {"initial": 0, "fixed": None, "remaining": 0}, ValueError),
        (FixMeasured, {"initial": 0, "fixed": 0, "remaining": None}, ValueError),
        (FixMeasured, {"initial": 1, "fixed": 0, "remaining": 0}, ValueError),
        (FixMeasured, {"initial": -1, "fixed": -1, "remaining": 0}, ValueError),
        (FixMeasured, {"initial": True, "fixed": 1, "remaining": 0}, ValueError),
        (FixPartial, {"initial": 0, "fixed": 0, "remaining": 0}, ValueError),
        (FixPartial, {"remaining": -1}, ValueError),
        (Checked, {"success": None}, ValueError),
        (Checked, {"success": True, "inspected_files": "unknown"}, ValueError),
        (TimedOut, {"output": 1}, ValueError),
        (
            ToolOutcome,
            {"execution": Checked(True), "accounting": FixUnmeasured("failed")},
            ValueError,
        ),
        (
            ToolOutcome,
            {"execution": NoFiles(), "accounting": FixUnmeasured("failed")},
            ValueError,
        ),
        (ToolOutcome, {"execution": Skipped("missing"), "issues_count": 1}, ValueError),
        (
            ToolOutcome,
            {"execution": NoFiles(), "accounting": FixMeasured(1, 1, 0)},
            ValueError,
        ),
        (ToolOutcome, {"execution": Checked(False), "issues_count": None}, ValueError),
        (ToolOutcome, {"execution": Checked(False), "issues_count": -1}, ValueError),
        (ToolOutcome, {"execution": Checked(False), "issues_count": True}, ValueError),
        (ToolOutcome, {"execution": "checked"}, TypeError),
        (ToolOutcome, {"execution": Checked(False), "accounting": None}, TypeError),
    ],
)
def test_invalid_explicit_outcomes_cannot_be_constructed(
    factory: Callable[..., object],
    payload: dict[str, Any],
    error: type[Exception],
) -> None:
    """Required evidence and state-specific counts are enforced on construction.

    Args:
        factory: Variant or combined outcome constructor.
        payload: Contradictory or malformed constructor arguments.
        error: Required boundary failure.
    """
    assert_that(factory).raises(error).when_called_with(**payload)


@pytest.mark.parametrize(
    "mutations",
    [
        {"skipped": True, "success": True},
        {"skip_reason": "orphan reason"},
        {"skipped": True, "skip_reason": "missing", "inspected_files": True},
        {"no_files": True, "success": True, "inspected_files": True},
        {"no_files": True, "success": True, "timed_out": True},
        {"no_files": True, "success": False},
        {"timed_out": True, "success": True},
        {"timed_out": True, "inspected_files": True},
        {"skipped": 1, "skip_reason": "missing", "success": True},
        {"success": 1},
        {"residual_unknown": "unknown", "residual_unknown_reason": "failed"},
        {"residual_unknown": True},
        {"residual_unknown_reason": "orphan reason"},
        {
            "residual_unknown": True,
            "residual_unknown_reason": "failed",
            "fixed_issues_count": 0,
        },
        {
            "residual_unknown": True,
            "residual_unknown_reason": "failed",
            "remaining_issues_count": 0,
        },
        {
            "residual_unknown": True,
            "residual_unknown_reason": "failed",
            "success": True,
        },
        {
            "initial_issues_count": 1,
            "fixed_issues_count": 0,
            "remaining_issues_count": 0,
        },
    ],
)
def test_invalid_replacement_is_rejected_without_changing_original(
    mutations: dict[str, Any],
) -> None:
    """Replacing several fields must satisfy the whole outcome contract.

    Args:
        mutations: Contradictory state proposed for an existing valid result.
    """
    result = ToolResult(inspected_files=False)
    before = asdict(result)
    assert_that(replace).raises(ValueError).when_called_with(result, **mutations)
    assert_that(asdict(result)).is_equal_to(before)


@pytest.mark.parametrize(
    ("value", "attribute"),
    [
        (Checked(False), "success"),
        (Skipped("missing"), "reason"),
        (NoFiles(), "extra_flag"),
        (TimedOut(None), "output"),
        (FixMeasured(1, 1, 0), "initial"),
        (FixPartial(initial=1), "remaining"),
        (FixUnmeasured("failed"), "fixed"),
        (ToolOutcome(Checked(False)), "execution"),
    ],
)
def test_every_outcome_value_is_frozen(value: Any, attribute: str) -> None:
    """Neither payloads nor extra flags can be attached to explicit states.

    Args:
        value: One instance of each frozen state and their composition.
        attribute: Payload or additional flag that must not be writable.
    """
    before = asdict(value)
    with pytest.raises(FrozenInstanceError):
        setattr(value, attribute, True)
    assert_that(asdict(value)).is_equal_to(before)


def test_outcome_property_cannot_be_overwritten_or_cached() -> None:
    """Frozen replacements remain the sole source of truth for the computed outcome."""
    result = ToolResult(success=True)
    assert_that(setattr).raises(AttributeError).when_called_with(
        result,
        "outcome",
        ToolOutcome(Checked(False)),
    )
    result = replace(result, success=False, issues_count=3)

    assert_that(result.outcome).is_equal_to(ToolOutcome(Checked(False), issues_count=3))
    assert_that(vars(result)).does_not_contain_key("outcome")


@pytest.mark.parametrize("field_name", [field.name for field in fields(ToolResult)])
def test_tool_result_fields_cannot_be_reassigned_or_deleted(field_name: str) -> None:
    """Every envelope and state field is frozen, including optional payloads.

    Args:
        field_name: Dataclass field whose assignment and deletion must fail.
    """
    result = ToolResult(name="ruff", inspected_files=None)
    before = asdict(result)
    assert_that(setattr).raises(FrozenInstanceError).when_called_with(
        result,
        field_name,
        getattr(result, field_name),
    )
    assert_that(delattr).raises(FrozenInstanceError).when_called_with(
        result,
        field_name,
    )
    assert_that(asdict(result)).is_equal_to(before)


def test_atomic_replacement_validates_counts_and_preserves_envelope() -> None:
    """An isolated count change fails; a consistent replacement retains payloads."""
    original = ToolResult.from_outcome(
        ToolOutcome(Checked(False), FixMeasured(3, 1, 2), issues_count=2),
        name="ruff",
        metadata={"suppressions": []},
        cwd="/repo",
        duration_seconds=1.5,
    )
    assert_that(replace).raises(ValueError).when_called_with(
        original,
        remaining_issues_count=0,
    )
    updated = replace(
        original,
        success=True,
        issues_count=0,
        fixed_issues_count=3,
        remaining_issues_count=0,
    )
    assert_that(updated.outcome).is_equal_to(
        ToolOutcome(Checked(True), FixMeasured(3, 3, 0)),
    )
    assert_that(updated.metadata).is_same_as(original.metadata)
    assert_that((updated.name, updated.cwd, updated.duration_seconds)).is_equal_to(
        ("ruff", "/repo", 1.5),
    )
    assert_that(original.outcome).is_equal_to(
        ToolOutcome(Checked(False), FixMeasured(3, 1, 2), issues_count=2),
    )


@pytest.mark.parametrize(
    ("payload", "execution"),
    [
        ({"skipped": True, "skip_reason": "not installed"}, Skipped("not installed")),
        (
            {"skipped": True, "skip_reason": "not installed", "success": False},
            Skipped("not installed"),
        ),
        ({"no_files": True, "success": True}, NoFiles()),
        ({"timed_out": True, "success": False}, TimedOut("plugin output")),
    ],
)
def test_api_v2_inactive_constructor_defaults_survive_replacement(
    payload: dict[str, Any],
    execution: Skipped | NoFiles | TimedOut,
) -> None:
    """Legacy constructor defaults remain usable by plugins and immutable updates.

    Args:
        payload: Common API-v2 plugin constructor arguments.
        execution: Expected normalized execution evidence.
    """
    result = ToolResult(name="plugin", output="plugin output", **payload)
    updated = replace(result, duration_seconds=1.25)

    assert_that(updated.outcome.execution).is_equal_to(execution)
    assert_that(updated.inspected_files).is_false()
    assert_that(updated.success).is_equal_to(not isinstance(execution, TimedOut))
    assert_that(updated.output).is_equal_to("plugin output")
    assert_that(updated.duration_seconds).is_equal_to(1.25)
    assert_that(result.duration_seconds).is_none()


def test_replacement_shares_mutable_metadata_without_changing_frozen_state() -> None:
    """AI metadata updates remain visible through both shallowly shared envelopes."""
    original = ToolResult(name="plugin", metadata={"suppressions": []})
    updated = original.with_outcome(ToolOutcome(Checked(True)))
    updated.metadata["fixed_count"] = 2
    updated.metadata["suppressions"].append({"code": "IGNORED"})

    assert_that(original.metadata).is_same_as(updated.metadata)
    assert_that(original.metadata).is_equal_to(
        {"fixed_count": 2, "suppressions": [{"code": "IGNORED"}]},
    )
    assert_that(original.success).is_false()
    assert_that(updated.success).is_true()
