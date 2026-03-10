"""Multi-turn fix refinement for unverified AI fixes.

When validation shows that an applied fix did not resolve the original
issue, this module reverts the fix, generates a refined suggestion using
the refinement prompt (which includes the previous attempt and the
validation error), applies the refined fix, and returns it for
re-validation.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.apply import apply_fixes
from lintro.ai.fix import (
    _call_provider,
    _extract_context,
    _parse_fix_response,
    _read_file_safely,
)
from lintro.ai.paths import to_provider_path
from lintro.ai.prompts import FIX_SYSTEM, REFINEMENT_PROMPT_TEMPLATE
from lintro.ai.retry import with_retry
from lintro.ai.sanitize import make_boundary_marker, sanitize_code_content

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.models import AIFixSuggestion
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.validation import ValidationResult


def _revert_fix(
    suggestion: AIFixSuggestion,
    workspace_root: Path,
) -> bool:
    """Revert an applied fix by replacing suggested_code back with original_code.

    Args:
        suggestion: The applied suggestion to revert.
        workspace_root: Workspace root for path resolution.

    Returns:
        True if the revert succeeded.
    """
    from lintro.ai.models import AIFixSuggestion as _Suggestion

    # Build a "reverse" suggestion: swap original and suggested code
    reverse = _Suggestion(
        file=suggestion.file,
        line=suggestion.line,
        code=suggestion.code,
        original_code=suggestion.suggested_code,
        suggested_code=suggestion.original_code,
    )
    applied = apply_fixes(
        [reverse],
        workspace_root=workspace_root,
        auto_apply=True,
    )
    return len(applied) > 0


def refine_unverified_fixes(
    *,
    applied_suggestions: list[AIFixSuggestion],
    validation: ValidationResult,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    workspace_root: Path,
) -> tuple[list[AIFixSuggestion], float]:
    """Attempt one refinement round for unverified fixes.

    For each unverified fix:
    1. Revert the original suggestion
    2. Generate a refined fix using the refinement prompt
    3. Apply the refined fix

    Args:
        applied_suggestions: All suggestions that were applied.
        validation: Validation result identifying unverified fixes.
        provider: AI provider instance.
        ai_config: AI configuration.
        workspace_root: Workspace root path.

    Returns:
        Tuple of (list of successfully refined suggestions, total cost).

    Raises:
        KeyboardInterrupt: Re-raised immediately.
        SystemExit: Re-raised immediately.
    """
    # Identify unverified suggestions from validation details
    unverified_keys: set[tuple[str, int]] = set()
    for detail in validation.details:
        if "issue still present" in detail:
            # Detail format: "[code] file:line - issue still present"
            # Parse code and file:line from the detail string
            try:
                bracket_end = detail.index("]")
                code = detail[1:bracket_end]
                rest = detail[bracket_end + 2 :]
                colon_idx = rest.index(":")
                # Find the space/dash separator after line number
                space_idx = rest.index(" ", colon_idx)
                line = int(rest[colon_idx + 1 : space_idx])
                unverified_keys.add((code, line))
            except (ValueError, IndexError):
                logger.debug("Skipping unparseable validation detail: {}", detail)
                continue

    if not unverified_keys:
        return [], 0.0

    bound_call = functools.partial(
        _call_provider,
        fallback_models=ai_config.fallback_models or [],
    )
    retrying_call = with_retry(
        max_retries=ai_config.max_retries,
        base_delay=ai_config.retry_base_delay,
        max_delay=ai_config.retry_max_delay,
        backoff_factor=ai_config.retry_backoff_factor,
    )(bound_call)

    refined: list[AIFixSuggestion] = []
    total_cost = 0.0

    for suggestion in applied_suggestions:
        if (suggestion.code, suggestion.line) not in unverified_keys:
            continue

        logger.debug(
            f"Refinement: reverting {suggestion.file}:{suggestion.line} "
            f"[{suggestion.code}]",
        )

        # Step 1: Revert the fix
        if not _revert_fix(suggestion, workspace_root):
            logger.debug(
                f"Refinement: revert failed for "
                f"{suggestion.file}:{suggestion.line}",
            )
            continue

        # Step 2: Read current file content and build refinement prompt
        file_content = _read_file_safely(suggestion.file)
        if file_content is None:
            continue

        context, context_start, context_end = _extract_context(
            file_content,
            suggestion.line,
            context_lines=ai_config.context_lines,
        )

        previous_suggestion = (
            f"original_code: {suggestion.original_code}\n"
            f"suggested_code: {suggestion.suggested_code}\n"
            f"explanation: {suggestion.explanation}"
        )

        # Find the matching validation detail for the error message
        error_detail = ""
        code_tag = f"[{suggestion.code}]"
        line_tag = f":{suggestion.line} "
        for detail in validation.details:
            if code_tag in detail and line_tag in detail:
                error_detail = detail
                break

        boundary = make_boundary_marker()
        prompt = REFINEMENT_PROMPT_TEMPLATE.format(
            tool_name=suggestion.tool_name or "unknown",
            code=suggestion.code,
            file=to_provider_path(suggestion.file, workspace_root),
            line=suggestion.line,
            previous_suggestion=previous_suggestion,
            new_error=error_detail or "Issue still present after fix",
            context_start=context_start,
            context_end=context_end,
            code_context=sanitize_code_content(context),
            boundary=boundary,
        )

        # Step 3: Generate refined fix
        try:
            response = retrying_call(
                provider,
                prompt,
                FIX_SYSTEM,
                ai_config.max_tokens,
                ai_config.api_timeout,
            )

            refined_suggestion = _parse_fix_response(
                response.content,
                suggestion.file,
                suggestion.line,
                suggestion.code,
            )

            if refined_suggestion is None:
                logger.debug(
                    f"Refinement: no valid suggestion for "
                    f"{suggestion.file}:{suggestion.line}",
                )
                continue

            refined_suggestion.tool_name = suggestion.tool_name
            refined_suggestion.input_tokens = response.input_tokens
            refined_suggestion.output_tokens = response.output_tokens
            refined_suggestion.cost_estimate = response.cost_estimate
            total_cost += response.cost_estimate

            # Step 4: Apply the refined fix
            applied = apply_fixes(
                [refined_suggestion],
                workspace_root=workspace_root,
                auto_apply=True,
                search_radius=ai_config.fix_search_radius,
            )
            if applied:
                refined.extend(applied)
                logger.debug(
                    f"Refinement: applied refined fix for "
                    f"{suggestion.file}:{suggestion.line}",
                )
            else:
                logger.debug(
                    f"Refinement: refined fix failed to apply for "
                    f"{suggestion.file}:{suggestion.line}",
                )

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.debug(
                f"Refinement failed for {suggestion.file}:{suggestion.line} "
                f"({type(exc).__name__}: {exc})",
                exc_info=True,
            )

    return refined, total_cost
