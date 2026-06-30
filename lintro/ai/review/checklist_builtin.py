"""Built-in review checklist items (Tier 1 and Tier 2).

Tier 2 items activate on two axes derived from the changed files: role
``domains`` owned by the file classifier and ``identify`` ``languages`` tags.
TypeScript/JavaScript variants are treated as one language family so an item
need not enumerate every extension.
"""

from __future__ import annotations

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.checklist_item import ChecklistItem

_TS_JS_LANGUAGES: tuple[str, ...] = ("javascript", "jsx", "ts", "tsx")

BUILTIN_CHECKLIST_ITEMS: tuple[ChecklistItem, ...] = (
    # Tier 1 — always included (IDs 1-15)
    ChecklistItem(
        id=1,
        question=(
            "Does any early return skip independent work that could proceed "
            "when an optional step fails?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.INTEGRATION,
        tier=1,
    ),
    ChecklistItem(
        id=2,
        question=(
            "Do test setup/fixture defaults match production/workflow/script "
            "defaults?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.TEST_GAP,
        tier=1,
    ),
    ChecklistItem(
        id=3,
        question=(
            "Does documentation claim behavior the implementation does not provide?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=1,
    ),
    ChecklistItem(
        id=4,
        question=(
            "Does any error path exit or return success when callers expect failure?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.SILENT_FAILURE,
        tier=1,
    ),
    ChecklistItem(
        id=5,
        question=("Do new defaults break existing callers without migration guidance?"),
        domains=(),
        languages=(),
        category=ReviewCategory.BREAKING_CHANGE,
        tier=1,
    ),
    ChecklistItem(
        id=6,
        question=(
            "Must two or more files be updated in lockstep for the change to "
            "stay correct?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=1,
    ),
    ChecklistItem(
        id=7,
        question=(
            "Does the diff implement behavior that already exists elsewhere "
            "(duplicate logic)?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=1,
    ),
    ChecklistItem(
        id=8,
        question="Does the diff actually do what the PR description claims?",
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=1,
    ),
    ChecklistItem(
        id=9,
        question=(
            "Does any unknown/unparseable enum or status default to permissive "
            "access (fail-open)?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.SECURITY,
        tier=1,
    ),
    ChecklistItem(
        id=10,
        question=(
            "Does the client classify server errors using fragile substring "
            "matching on prose messages?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=1,
    ),
    ChecklistItem(
        id=11,
        question=(
            "Does the server return prose-only errors without stable "
            "machine-readable error codes?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=1,
    ),
    ChecklistItem(
        id=12,
        question=(
            "Are there visibility-only tests without asserting content, state, "
            "or copy?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.TEST_GAP,
        tier=1,
    ),
    ChecklistItem(
        id=13,
        question=(
            "Do tests mock internals/private methods such that behavior breaks "
            "could still pass?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.TEST_GAP,
        tier=1,
    ),
    ChecklistItem(
        id=14,
        question=(
            "Does the diff implement behavior contradicting explicitly deferred/"
            "out-of-scope items in the PR summary?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.INTEGRATION,
        tier=1,
    ),
    ChecklistItem(
        id=15,
        question=(
            "Does API/client code use type casts on responses without runtime "
            "validation?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.SILENT_FAILURE,
        tier=1,
    ),
    # Tier 2 — domain/language-triggered (IDs 100+)
    ChecklistItem(
        id=100,
        question=(
            "Are failures handled consistently across all branches "
            "(no mixed throw/return/silent paths)?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=101,
        question=(
            "Are connections, file handles, streams, or other resources closed "
            "on every exit path?"
        ),
        domains=(FileDomain.SOURCE,),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=102,
        question=(
            "Does concurrent or async code have race conditions, deadlocks, "
            "or missing cleanup?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=103,
        question=(
            "Does library code use .unwrap() or bare .expect() without a "
            "documented invariant?"
        ),
        domains=(),
        languages=("rust",),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=104,
        question=(
            "Does an unsafe block lack a // SAFETY: justification for soundness?"
        ),
        domains=(),
        languages=("rust",),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=105,
        question=(
            "Can a caller ignore a return value that must not be ignored "
            "(missing #[must_use] or equivalent)?"
        ),
        domains=(),
        languages=("rust",),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=106,
        question=("Does a catch block use any and fail to narrow before handling?"),
        domains=(),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=107,
        question=(
            "Does an empty catch block swallow errors without logging or rethrow?"
        ),
        domains=(),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=108,
        question=(
            "Are there unreachable branches or dead code paths that suggest "
            "incorrect control flow?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=109,
        question=(
            "Does shell script logic lack set -euo pipefail (or equivalent) "
            "where failures should abort?"
        ),
        domains=(FileDomain.SHELL,),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=110,
        question=(
            "Do E2E tests use fixed waitForTimeout() instead of condition-based "
            "waiting (risk of flaky false passes/fails)?"
        ),
        domains=(FileDomain.E2E,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=111,
        question=(
            "Do E2E tests read DOM state once without auto-retrying assertions "
            "(timing-dependent false results)?"
        ),
        domains=(FileDomain.E2E,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=112,
        question=(
            "Do shell tests call real network/external commands instead of mocks?"
        ),
        domains=(),
        languages=("bats",),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=113,
        question=(
            "Do shell tests assume Bash 4+ features without skipping on Bash 3.2?"
        ),
        domains=(),
        languages=("bats",),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=114,
        question=(
            "Are PII, secrets, or tokens logged, returned in errors, or exposed "
            "in API responses?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=115,
        question=(
            'Does a test assert field == "literal" where that literal is also '
            "defined in production code (wiring test that drifts silently)?"
        ),
        domains=(FileDomain.TEST,),
        languages=("python",),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=116,
        question=(
            "Does a non-strict schema (z.object) silently accept unexpected "
            "API fields?"
        ),
        domains=(FileDomain.API,),
        languages=("ts", "tsx"),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=117,
        question=(
            "Are duplicate type definitions maintained separately from "
            "validation schemas (can drift undetected)?"
        ),
        domains=(FileDomain.API,),
        languages=("ts", "tsx"),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=118,
        question=(
            "Does CI verify two hand-maintained files agree instead of running "
            "a generator with --check?"
        ),
        domains=(FileDomain.CI,),
        languages=(),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=119,
        question=(
            "Does an automation hook (e.g. Renovate post-upgrade) silently fail "
            "while CI is supposed to catch drift—is the --check gate still present?"
        ),
        domains=(FileDomain.CI,),
        languages=(),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=120,
        question=(
            "Do bulk/planned-ahead tests assert signatures or data shapes instead "
            "of observable behavior?"
        ),
        domains=(FileDomain.TEST,),
        languages=(),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=121,
        question=(
            "Would renaming an internal function break tests even though "
            "user-visible behavior is unchanged?"
        ),
        domains=(FileDomain.TEST,),
        languages=(),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=122,
        question=(
            "Are lint/security suppressions (noqa, type: ignore, allow(dead_code), "
            "# nosec) added without reviewing the underlying risk?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=123,
        question=(
            "Does fixture cleanup swallow delete/teardown errors (.catch(() => {})) "
            "hiding partial test pollution?"
        ),
        domains=(FileDomain.TEST,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=124,
        question=(
            "Is substantial shell logic inlined in workflow YAML instead of a "
            "dedicated script file?"
        ),
        domains=(FileDomain.CI,),
        languages=(),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=125,
        question=(
            "Are CI scripts referenced from workflows missing a shebang or "
            "executable bit?"
        ),
        domains=(FileDomain.CI, FileDomain.SHELL),
        languages=(),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=126,
        question=(
            "Does HTTP request logic live in fixtures instead of a dedicated "
            "client layer?"
        ),
        domains=(FileDomain.TEST, FileDomain.API),
        languages=(),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=127,
        question=(
            "Are API base paths duplicated across clients instead of centralized?"
        ),
        domains=(FileDomain.API,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=128,
        question=(
            "Do E2E tests share mutable global state across iterations "
            "(order-dependent failures)?"
        ),
        domains=(FileDomain.E2E,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=129,
        question=(
            "Are browser contexts/fixtures left open without teardown "
            "(context.close())?"
        ),
        domains=(FileDomain.E2E,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=130,
        question=(
            "Do shell tests leave temp files or env changes behind (missing teardown)?"
        ),
        domains=(),
        languages=("bats",),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=131,
        question=(
            "Are pure functions unit-tested while the bug-prone call-site wiring "
            "has no integration coverage?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=132,
        question=(
            "Do tests hit the wrong layer (e.g. DB directly instead of the public "
            "API under test)?"
        ),
        domains=(FileDomain.TEST,),
        languages=(),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=133,
        question=(
            "Does new or changed production code lack corresponding test updates "
            "in the same diff?"
        ),
        domains=(),
        languages=(*_TS_JS_LANGUAGES, "python", "rust"),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=134,
        question=("Does new or changed logic lack tests covering the modified paths?"),
        domains=(FileDomain.SOURCE,),
        languages=(*_TS_JS_LANGUAGES, "python", "rust"),
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=135,
        question=(
            "Are edge cases and failure modes (empty input, invalid input, "
            "missing files) untested?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=136,
        question=(
            "Are error-response tests missing or only checking one field per "
            "assertion (hiding partial regressions)?"
        ),
        domains=(FileDomain.TEST,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=137,
        question=(
            "Are security edge cases untested (injection payloads, malformed "
            "input, oversized payloads)?"
        ),
        domains=(FileDomain.TEST, FileDomain.API),
        languages=(*_TS_JS_LANGUAGES, "python"),
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=138,
        question=(
            "Are parameterized cases missing where multiple inputs share the "
            "same behavior rule?"
        ),
        domains=(FileDomain.TEST,),
        languages=(*_TS_JS_LANGUAGES, "bats", "python"),
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=139,
        question=(
            "Are tests non-deterministic (timing, network, shared state, random "
            "without seed)?"
        ),
        domains=(FileDomain.TEST,),
        languages=("bats",),
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=140,
        question=(
            "Does the test name describe something different from what is "
            "actually asserted?"
        ),
        domains=(FileDomain.TEST,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=141,
        question=(
            "Are expected panics/failures tested with #[should_panic] or "
            "equivalent where invalid input must fail?"
        ),
        domains=(FileDomain.TEST,),
        languages=("rust",),
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=142,
        question=(
            "Do exit-code paths in shell scripts lack assert_failure / status checks?"
        ),
        domains=(FileDomain.SHELL,),
        languages=("bats",),
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=143,
        question=(
            "Do parametrize/test IDs embed mutable version or data values that "
            "will churn on every bump?"
        ),
        domains=(FileDomain.TEST,),
        languages=("python",),
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=2,
    ),
    ChecklistItem(
        id=144,
        question=(
            "Are E2E/UI tests hard-coding labels, error strings, or IDs that "
            "should come from shared constants/enums?"
        ),
        domains=(FileDomain.E2E,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=2,
    ),
    ChecklistItem(
        id=145,
        question=(
            "Does a public interface change lack updates to callers, schemas, "
            "or contract tests?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=2,
    ),
    ChecklistItem(
        id=146,
        question=(
            "Are credentials, API keys, tokens, or passwords hardcoded in source?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=147,
        question=(
            "Is user input interpolated into SQL/command strings instead of "
            "parameterized/safe APIs?"
        ),
        domains=(),
        languages=(*_TS_JS_LANGUAGES, "go", "java", "python", "rust"),
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=148,
        question=(
            "Is subprocess invoked with shell interpretation enabled "
            "(shell=True or equivalent)?"
        ),
        domains=(FileDomain.SHELL,),
        languages=("python",),
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=149,
        question=(
            "Is input validated and sanitized at system boundaries (HTTP handlers, "
            "CLI args, file uploads)?"
        ),
        domains=(FileDomain.API,),
        languages=(*_TS_JS_LANGUAGES, "go", "java", "python", "rust"),
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=150,
        question=(
            "Are GitHub Actions referenced by mutable version tags instead of "
            "pinned commit SHAs?"
        ),
        domains=(FileDomain.CI,),
        languages=(),
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=151,
        question=(
            "Does a # nosec / security suppression lack an inline justification "
            "on the flagged line?"
        ),
        domains=(),
        languages=("python",),
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=152,
        question=(
            "Does removing or renaming exported/public symbols lack migration "
            "or caller updates?"
        ),
        domains=(),
        languages=(*_TS_JS_LANGUAGES, "python", "rust"),
        category=ReviewCategory.BREAKING_CHANGE,
        tier=2,
    ),
    ChecklistItem(
        id=153,
        question=(
            "Does a schema or API response change break strict contract tests "
            "or downstream parsers?"
        ),
        domains=(FileDomain.API,),
        languages=(),
        category=ReviewCategory.BREAKING_CHANGE,
        tier=2,
    ),
    ChecklistItem(
        id=154,
        question=(
            "Is type safety bypassed with any, unchecked casts, or broad "
            "suppressions on changed lines?"
        ),
        domains=(),
        languages=(*_TS_JS_LANGUAGES, "python", "rust"),
        category=ReviewCategory.CODE_SMELL,
        tier=2,
    ),
    ChecklistItem(
        id=155,
        question=(
            "Do E2E tests rely on fragile CSS selectors (#id, deep div > button) "
            "likely to break on unrelated UI changes?"
        ),
        domains=(FileDomain.E2E,),
        languages=_TS_JS_LANGUAGES,
        category=ReviewCategory.CODE_SMELL,
        tier=2,
    ),
    ChecklistItem(
        id=156,
        question=(
            "Is the module interface nearly as complex as its implementation "
            "(shallow module)?"
        ),
        domains=(),
        languages=(),
        category=ReviewCategory.ARCHITECTURE,
        tier=2,
    ),
    ChecklistItem(
        id=157,
        question=(
            "Are tests written against implementation steps rather than "
            "user-/caller-visible behavior?"
        ),
        domains=(FileDomain.TEST,),
        languages=(),
        category=ReviewCategory.ARCHITECTURE,
        tier=2,
    ),
)

TIER1_CHECKLIST_ITEMS: tuple[ChecklistItem, ...] = tuple(
    item for item in BUILTIN_CHECKLIST_ITEMS if item.tier == 1
)

TIER2_CHECKLIST_ITEMS: tuple[ChecklistItem, ...] = tuple(
    item for item in BUILTIN_CHECKLIST_ITEMS if item.tier == 2
)
