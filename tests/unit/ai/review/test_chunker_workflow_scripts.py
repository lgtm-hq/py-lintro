"""Tests for workflow script reference grouping."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.chunking_result import ChunkingResult
from tests.unit.ai.review.review_fixtures import (
    load_review_fixture,
    make_review_context,
)


def _assert_script_grouped_separately_from_workflow(
    *,
    result: ChunkingResult,
    script_path: str,
    workflow_path: str = ".github/workflows/ci.yml",
) -> None:
    """Assert a script changed with a workflow lands in its own chunk."""
    script_chunks = [chunk for chunk in result.chunks if script_path in chunk.files]
    assert_that(script_chunks).is_length(1)
    assert_that(script_chunks[0].files).does_not_contain(workflow_path)


def test_chunker_groups_workflow_with_suffixless_bin_executable() -> None:
    """Suffixless executables under bin/ can be referenced from workflows."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_suffixless_bin_executable.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="bin/lintro",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("bin/lintro")


def test_chunker_groups_nested_workflow_with_referenced_script() -> None:
    """Nested reusable workflows match workflow glob grouping heuristics."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_nested_workflow_with_referenced_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/actions/reusable.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/ci/run.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk
        for chunk in result.chunks
        if ".github/workflows/actions/reusable.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/workflows/actions/reusable.yml",
        "scripts/ci/run.sh",
    )


def test_chunker_groups_workflow_with_local_action_directory_reference() -> None:
    """Local action directory references match changed action implementation files."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_local_action_directory_reference.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/index.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/workflows/ci.yml",
        ".github/actions/setup/index.js",
    )


def test_chunker_groups_workflow_with_action_dist_implementation() -> None:
    """Nested action build output resolves to the action root for ``uses:`` matching."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_action_dist_implementation.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/dist/index.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/workflows/ci.yml",
        ".github/actions/setup/dist/index.js",
    )
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_action_dist_chunks_implementation() -> None:
    """Deep build output under artifact dirs resolves to the action root."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_action_dist_chunks_implementation.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/dist/chunks/index.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/actions/setup/dist/chunks/index.js",
    )
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_action_dist_manifest() -> None:
    """Action manifests under artifact dirs resolve to the action root."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_action_dist_manifest.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/dist/action.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/actions/setup/dist/action.yml",
    )
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_node_script() -> None:
    """``node`` runtime prefixes match changed script paths."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_node_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/build.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/build.js")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_bun_script() -> None:
    """``bun`` runtime prefixes match changed script paths."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bun_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.ts",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.ts")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_bun_run_script() -> None:
    """``bun run`` dispatch wrappers match changed script paths."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bun_run_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.ts",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.ts")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_node_loader_script() -> None:
    """Node runtime operand flags are skipped before resolving the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_node_loader_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/build.ts",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/build.ts")
    assert_that(result.warnings).is_empty()


def test_chunker_does_not_group_script_after_node_version_flag() -> None:
    """Terminating runtime flags do not execute trailing script tokens."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_group_script_after_node_version_flag.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/build.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/build.js")
    _assert_script_grouped_separately_from_workflow(
        result=result,
        script_path="scripts/build.js",
    )


def test_chunker_groups_workflow_with_uv_run_with_editable_script() -> None:
    """``uv run --with-*`` operands are skipped before resolving the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_with_editable_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_action_src_implementation() -> None:
    """Source layout dirs under an action resolve to the action root."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_action_src_implementation.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/src/index.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/actions/setup/src/index.js",
    )
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_nested_action_src_commands() -> None:
    """Nested action implementation files under src/... resolve to the action root."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_nested_action_src_commands.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/src/commands/run.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/actions/setup/src/commands/run.js",
    )
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_nested_team_setup_dist() -> None:
    """Nested action artifact paths resolve to the nested action root."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_nested_team_setup_dist.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/team/setup/dist/index.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/actions/team/setup/dist/index.js",
    )
    assert_that(result.warnings).is_empty()


def test_chunker_does_not_group_internal_dist_with_parent_uses() -> None:
    """Artifact paths under nested siblings do not match parent action uses."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_group_internal_dist_with_parent_uses.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/internal/dist/index.js",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain(
        ".github/actions/setup/internal/dist/index.js",
    )
    _assert_script_grouped_separately_from_workflow(
        result=result,
        script_path=".github/actions/setup/internal/dist/index.js",
    )


def test_chunker_groups_workflow_with_uv_run_quiet_script() -> None:
    """``uv run -q`` short flags are skipped before resolving the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_quiet_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_uv_run_python_short_script() -> None:
    """``uv run -p`` operands are skipped before resolving the invoked script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_python_short_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_workspace_prefixed_script() -> None:
    """Workflow run steps with github.workspace prefixes match script paths."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_workspace_prefixed_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/ci/run.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/ci/run.sh")


def test_chunker_groups_workflow_with_bash_wrapped_script() -> None:
    """Workflow run steps that invoke scripts via bash/sh wrappers still group."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bash_wrapped_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_bash_flag_wrapped_script() -> None:
    """Single-line run steps with shell flags before the script path still group."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bash_flag_wrapped_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_bash_equals_flag_script() -> None:
    """Single-line run steps with ``--flag=value`` options still group scripts."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bash_equals_flag_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_bash_pipefail_wrapped_script() -> None:
    """Single-line run steps with valued shell flags still group scripts."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bash_pipefail_wrapped_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_uv_run_python_script() -> None:
    """Uv run python invocations still group the referenced script path."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_python_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_action_dockerfile() -> None:
    """Container action Dockerfiles are treated as workflow-linked files."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_action_dockerfile.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/build/Dockerfile",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(".github/actions/build/Dockerfile")


def test_chunker_groups_workflow_with_action_entrypoint_script() -> None:
    """Composite action metadata that points at ``run`` groups with the entrypoint."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_action_entrypoint_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/action.yml",
                status="added",
                additions=7,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/run",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(".github/actions/setup/run")
    assert_that(workflow_group.files).contains(".github/actions/setup/action.yml")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_nested_local_action() -> None:
    """Nested local action directories resolve the full action path for uses: matching."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_nested_local_action.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/docker/build/Dockerfile",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/actions/docker/build/Dockerfile",
    )
    assert_that(result.warnings).is_empty()


def test_chunker_does_not_match_script_path_prefix_collisions() -> None:
    """Workflow script references require path boundaries, not substring matches."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_match_script_path_prefix_collisions.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh-old",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh-old")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh-old changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_uses_full_workflow_text_for_unchanged_script_reference() -> None:
    """Unchanged run: lines in post-image workflow text still group scripts."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_uses_full_workflow_text_for_unchanged_script_reference.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\nenv:\n  CI: true\nrun: scripts/deploy.sh\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_ignores_script_paths_in_workflow_comments() -> None:
    """Comment-only script mentions do not group unrelated script changes."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_ignores_script_paths_in_workflow_comments.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/old-deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/old-deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/old-deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_groups_workflow_with_multiline_run_block() -> None:
    """Scripts invoked inside run: | blocks are still grouped with workflows."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_multiline_run_block.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": "name: CI\nrun: |\n  scripts/deploy.sh\n",
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_adjacent_multiline_run_blocks() -> None:
    """Back-to-back multiline run blocks still scan later script invocations."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_adjacent_multiline_run_blocks.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\n"
                "run: |\n"
                "  echo setup\n"
                "run: |\n"
                "  scripts/deploy.sh\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_multiline_workspace_script() -> None:
    """Multiline run blocks honor github.workspace-prefixed script references."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_multiline_workspace_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\nrun: |\n  ${{ github.workspace }}/scripts/deploy.sh\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_does_not_match_run_prefix_collision() -> None:
    """run: lines with unrelated directory prefixes do not group script paths."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_match_run_prefix_collision.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_ignores_commented_run_reference() -> None:
    """Commented ``# run:`` lines do not group script changes with workflows."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_ignores_commented_run_reference.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_ignores_echo_mention_in_multiline_run_block() -> None:
    """Quoted or echoed script paths in run blocks are not treated as invocations."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_multiline_echo_mention_not_reference.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                'name: CI\nrun: |\n  echo "see scripts/deploy.sh"\n'
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_groups_workflow_with_uv_run_with_options() -> None:
    """``uv run --with ... python`` still groups the referenced script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_with_options.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_multiline_cd_relative_script() -> None:
    """Multiline blocks honor ``cd`` before cwd-relative script invocations."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_multiline_cd_relative_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\nrun: |\n  cd scripts\n  ./deploy.sh\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_quoted_run_script() -> None:
    """Single-line ``run:`` steps with quoted script paths still group scripts."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_quoted_run_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_multiline_quoted_script() -> None:
    """Multiline blocks still group scripts invoked via quoted arguments."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_multiline_quoted_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                'name: CI\nrun: |\n  bash "scripts/deploy.sh"\n'
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_inline_cd_run_script() -> None:
    """Single-line ``run: cd ... && ./script`` groups the referenced script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_inline_cd_run_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_deploy_before_cd_on_same_line() -> None:
    """``./deploy.sh && cd scripts`` resolves the script before the later ``cd``."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_inline_cd_run_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": ("name: CI\nrun: ./deploy.sh && cd scripts\n"),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_resets_cwd_between_multiline_run_blocks() -> None:
    """Each multiline ``run:`` block starts from the step's initial cwd."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_separate_run_blocks_do_not_share_cwd.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\nrun: |\n  cd scripts\nrun: |\n  ./deploy.sh\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_or_cd_does_not_persist_cwd_for_later_commands() -> None:
    """``cd missing || true`` does not move later relative script invocations."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_multiline_or_cd_does_not_persist_cwd.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\nrun: |\n  cd missing || true\n  ./deploy.sh\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_does_not_match_quoted_absolute_run_path() -> None:
    """Quoted ``/scripts/...`` paths are not treated as repo-root script references."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_match_quoted_absolute_run_path.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_ignores_grep_script_mention() -> None:
    """Read-only commands that mention a script path do not group the script."""
    context = make_review_context(
        unified_diff=load_review_fixture("chunk_ignores_grep_script_mention.diff"),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_ignores_bash_c_script_mention() -> None:
    """Paths inside ``bash -c`` command strings are not treated as invocations."""
    context = make_review_context(
        unified_diff=load_review_fixture("chunk_ignores_bash_c_script_mention.diff"),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_groups_workflow_with_uv_run_isolated_python() -> None:
    """Boolean ``uv run`` flags must not consume the interpreter token."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_isolated_python.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_indented_list_run_step() -> None:
    """YAML list-item ``- run:`` steps still group referenced scripts."""
    context = make_review_context(
        unified_diff=load_review_fixture("chunk_groups_indented_list_run_step.diff"),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_persists_cd_before_pipeline_in_multiline_block() -> None:
    """``cd`` before a pipeline persists for later lines in the same run block."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_multiline_cd_relative_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\nrun: |\n  cd scripts && cat README | wc -l\n"
                "  ./deploy.sh\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_python_warning_flag() -> None:
    """Interpreter flags with space-separated values still group the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_python_warning_flag.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_bash_c_unquoted_script() -> None:
    """Unquoted ``bash -c scripts/...`` executes the referenced script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bash_c_unquoted_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_ignores_chmod_script_mention() -> None:
    """File-permission commands that mention a script path do not group it."""
    context = make_review_context(
        unified_diff=load_review_fixture("chunk_ignores_chmod_script_mention.diff"),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )
    _assert_script_grouped_separately_from_workflow(
        result=result,
        script_path="scripts/deploy.sh",
    )


def test_chunker_does_not_match_parent_relative_run_path() -> None:
    """``../scripts/...`` paths do not match repo-root script references."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_match_parent_relative_run_path.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )
    _assert_script_grouped_separately_from_workflow(
        result=result,
        script_path="scripts/deploy.sh",
    )


def test_chunker_does_not_match_double_parent_relative_run_path() -> None:
    """``../../scripts/...`` paths do not collapse onto repo-root references."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_match_double_parent_relative_run_path.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )
    _assert_script_grouped_separately_from_workflow(
        result=result,
        script_path="scripts/deploy.sh",
    )


def test_chunker_ignores_commented_multiline_run_block() -> None:
    """Commented ``# run: |`` markers do not open multiline run-block scanning."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_ignores_commented_multiline_run_block.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": ("name: CI\n# run: |\n#   scripts/deploy.sh\n"),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )
    _assert_script_grouped_separately_from_workflow(
        result=result,
        script_path="scripts/deploy.sh",
    )


def test_chunker_groups_workflow_with_env_assignment_prefixed_script() -> None:
    """VAR=value prefixes before a script invocation still group the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_env_prefixed_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_env_command_prefixed_script() -> None:
    """``env VAR=value`` prefixes before a script invocation still group the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_env_command_prefixed_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_does_not_match_bash_s_script_argument() -> None:
    """``bash -s`` treats the script path as stdin input, not execution."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_match_bash_s_script_argument.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )
    _assert_script_grouped_separately_from_workflow(
        result=result,
        script_path="scripts/deploy.sh",
    )


def test_chunker_groups_workflow_with_bash_script_c_flag_argument() -> None:
    """Trailing ``-c`` flags on an invoked script are not ``bash -c`` payloads."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bash_script_c_flag_argument.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_ignores_dry_run_multiline_block() -> None:
    """Non-step keys ending in run: do not open multiline shell scanning."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_ignores_dry_run_multiline_block.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_ignores_uses_in_if_expression() -> None:
    """Embedded uses: text inside expressions is not a step reference."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_ignores_uses_in_if_expression.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_groups_indented_run_from_post_image_text() -> None:
    """Indented ``- run:`` steps in post-image workflow text still group scripts."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_uses_post_image_indented_run_command.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\nsteps:\n      - run: bash -c 'scripts/deploy.sh'\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_multiline_cd_fail_fast_script() -> None:
    """``cd dir || exit 1`` updates cwd for subsequent relative script invocations."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_multiline_cd_fail_fast_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_env_unset_prefixed_script() -> None:
    """``env -u VAR`` operands are skipped before resolving the invoked script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_env_unset_prefixed_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_uv_run_direct_script() -> None:
    """``uv run --with ... script.py`` without an interpreter still groups the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_direct_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_single_line_cd_fail_fast_script() -> None:
    """Single-line ``cd dir || exit 1; ./script`` preserves cwd for later commands."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_single_line_cd_fail_fast_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_ignores_run_text_in_name_field() -> None:
    """Embedded ``run:`` text in non-step YAML fields is not treated as a step key."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_ignores_run_text_in_name_field.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_chunker_groups_workflow_with_exec_prefixed_script() -> None:
    """``exec`` dispatch wrappers are skipped before resolving the invoked script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_exec_prefixed_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_if_then_script() -> None:
    """Scripts invoked in ``then`` compound-command segments still group correctly."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_if_then_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_env_chdir_prefixed_script() -> None:
    """``env --chdir`` operands are skipped before resolving the invoked script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_env_chdir_prefixed_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_uv_run_python_option_script() -> None:
    """``uv run --python`` operands are skipped before resolving the invoked script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_python_option_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_does_not_group_nested_action_with_parent_uses() -> None:
    """Parent ``uses:`` paths do not reference nested sibling action implementations."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_does_not_group_nested_action_with_parent_uses.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/internal/run",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain(
        ".github/actions/setup/internal/run",
    )
    assert_that(result.warnings).contains(
        "Script .github/actions/setup/internal/run changed alongside workflows "
        "but is not referenced in any changed workflow diff; grouped separately.",
    )
    _assert_script_grouped_separately_from_workflow(
        result=result,
        script_path=".github/actions/setup/internal/run",
    )


def test_chunker_groups_workflow_with_command_option_script() -> None:
    """``command --`` option separators are skipped before resolving the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_command_option_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_stacked_shell_wrappers() -> None:
    """Stacked compound leaders and dispatch wrappers still resolve the script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_stacked_shell_wrappers.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_env_bash_c_wrapped_script() -> None:
    """Wrapped ``env bash -c`` invocations still group the referenced script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_env_bash_c_wrapped_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_bash_clustered_c_flag_script() -> None:
    """Clustered ``bash -ec`` flags still parse the ``-c`` payload for script refs."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_bash_clustered_c_flag_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_python_verbose_flag() -> None:
    """Verbose runtime flags still execute trailing script tokens."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_python_verbose_flag.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_python_werror_flag() -> None:
    """Inline warning flags do not consume the script operand."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_python_werror_flag.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/review.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/review.py")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_node_attached_preload_script() -> None:
    """Attached ``-r`` preload operands do not consume the script token."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_node_attached_preload_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/build.ts",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/build.ts")
    assert_that(result.warnings).is_empty()


def test_chunker_groups_workflow_with_uv_run_bash_c_script() -> None:
    """Nested ``uv run bash -c`` payloads still group the referenced script."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_uv_run_bash_c_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains("scripts/deploy.sh")
    assert_that(result.warnings).is_empty()


def test_chunker_ignores_folded_run_echo_script_mention() -> None:
    """Folded ``run: >`` blocks join lines before checking script references."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_ignores_folded_run_echo_script_mention.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/deploy.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        post_image_files={
            ".github/workflows/ci.yml": (
                "name: CI\n- run: >\n    echo\n    scripts/deploy.sh\n"
            ),
        },
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).does_not_contain("scripts/deploy.sh")
    assert_that(result.warnings).contains(
        "Script scripts/deploy.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )
