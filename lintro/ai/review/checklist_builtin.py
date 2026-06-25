"""Built-in review checklist items (Tier 1 and Tier 2)."""

from __future__ import annotations

from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.checklist_item import ChecklistItem

_E2E_TEST_GLOBS: tuple[str, ...] = (
    "**/*.{spec,test}.{ts,js}",
    "**/e2e/**",
    "**/playwright-tests/**",
)

_TEST_GLOBS: tuple[str, ...] = (
    "**/*.{spec,test}.{ts,js,py}",
    "**/*.bats",
    "**/test_*.py",
    "**/*_test.py",
    "**/tests/**",
)

_TYPESCRIPT_GLOBS: tuple[str, ...] = ("**/*.{ts,tsx,js,jsx}",)

_SOURCE_GLOBS: tuple[str, ...] = ("**/*.{py,rs,ts,js,go,java}",)

BUILTIN_CHECKLIST_ITEMS: tuple[ChecklistItem, ...] = (
    # Tier 1 — always included (IDs 1-15)
    ChecklistItem(
        id=1,
        question=(
            "Does any early return skip independent work that could proceed "
            "when an optional step fails?"
        ),
        triggers=[],
        category=ReviewCategory.INTEGRATION,
        tier=1,
    ),
    ChecklistItem(
        id=2,
        question=(
            "Do test setup/fixture defaults match production/workflow/script "
            "defaults?"
        ),
        triggers=[],
        category=ReviewCategory.TEST_GAP,
        tier=1,
    ),
    ChecklistItem(
        id=3,
        question=(
            "Does documentation claim behavior the implementation does not provide?"
        ),
        triggers=[],
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=1,
    ),
    ChecklistItem(
        id=4,
        question=(
            "Does any error path exit or return success when callers expect failure?"
        ),
        triggers=[],
        category=ReviewCategory.SILENT_FAILURE,
        tier=1,
    ),
    ChecklistItem(
        id=5,
        question=("Do new defaults break existing callers without migration guidance?"),
        triggers=[],
        category=ReviewCategory.BREAKING_CHANGE,
        tier=1,
    ),
    ChecklistItem(
        id=6,
        question=(
            "Must two or more files be updated in lockstep for the change to "
            "stay correct?"
        ),
        triggers=[],
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=1,
    ),
    ChecklistItem(
        id=7,
        question=(
            "Does the diff implement behavior that already exists elsewhere "
            "(duplicate logic)?"
        ),
        triggers=[],
        category=ReviewCategory.LOGIC_BUG,
        tier=1,
    ),
    ChecklistItem(
        id=8,
        question="Does the diff actually do what the PR description claims?",
        triggers=[],
        category=ReviewCategory.LOGIC_BUG,
        tier=1,
    ),
    ChecklistItem(
        id=9,
        question=(
            "Does any unknown/unparseable enum or status default to permissive "
            "access (fail-open)?"
        ),
        triggers=[],
        category=ReviewCategory.SECURITY,
        tier=1,
    ),
    ChecklistItem(
        id=10,
        question=(
            "Does the client classify server errors using fragile substring "
            "matching on prose messages?"
        ),
        triggers=[],
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=1,
    ),
    ChecklistItem(
        id=11,
        question=(
            "Does the server return prose-only errors without stable "
            "machine-readable error codes?"
        ),
        triggers=[],
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=1,
    ),
    ChecklistItem(
        id=12,
        question=(
            "Are there visibility-only tests without asserting content, state, "
            "or copy?"
        ),
        triggers=[],
        category=ReviewCategory.TEST_GAP,
        tier=1,
    ),
    ChecklistItem(
        id=13,
        question=(
            "Do tests mock internals/private methods such that behavior breaks "
            "could still pass?"
        ),
        triggers=[],
        category=ReviewCategory.TEST_GAP,
        tier=1,
    ),
    ChecklistItem(
        id=14,
        question=(
            "Does the diff implement behavior contradicting explicitly deferred/"
            "out-of-scope items in the PR summary?"
        ),
        triggers=[],
        category=ReviewCategory.INTEGRATION,
        tier=1,
    ),
    ChecklistItem(
        id=15,
        question=(
            "Does API/client code use type casts on responses without runtime "
            "validation?"
        ),
        triggers=[],
        category=ReviewCategory.SILENT_FAILURE,
        tier=1,
    ),
    # Tier 2 — domain-triggered (IDs 100+)
    ChecklistItem(
        id=100,
        question=(
            "Are failures handled consistently across all branches "
            "(no mixed throw/return/silent paths)?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=101,
        question=(
            "Are connections, file handles, streams, or other resources closed "
            "on every exit path?"
        ),
        triggers=list(_SOURCE_GLOBS),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=102,
        question=(
            "Does concurrent or async code have race conditions, deadlocks, "
            "or missing cleanup?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=103,
        question=(
            "Does library code use .unwrap() or bare .expect() without a "
            "documented invariant?"
        ),
        triggers=["**/*.rs"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=104,
        question=(
            "Does an unsafe block lack a // SAFETY: justification for soundness?"
        ),
        triggers=["**/*.rs"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=105,
        question=(
            "Can a caller ignore a return value that must not be ignored "
            "(missing #[must_use] or equivalent)?"
        ),
        triggers=["**/*.rs"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=106,
        question=("Does a catch block use any and fail to narrow before handling?"),
        triggers=list(_TYPESCRIPT_GLOBS),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=107,
        question=(
            "Does an empty catch block swallow errors without logging or rethrow?"
        ),
        triggers=list(_TYPESCRIPT_GLOBS),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=108,
        question=(
            "Are there unreachable branches or dead code paths that suggest "
            "incorrect control flow?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=109,
        question=(
            "Does shell script logic lack set -euo pipefail (or equivalent) "
            "where failures should abort?"
        ),
        triggers=["**/*.{sh,bash}", "scripts/**/*"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=110,
        question=(
            "Do E2E tests use fixed waitForTimeout() instead of condition-based "
            "waiting (risk of flaky false passes/fails)?"
        ),
        triggers=list(_E2E_TEST_GLOBS),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=111,
        question=(
            "Do E2E tests read DOM state once without auto-retrying assertions "
            "(timing-dependent false results)?"
        ),
        triggers=list(_E2E_TEST_GLOBS),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=112,
        question=(
            "Do shell tests call real network/external commands instead of mocks?"
        ),
        triggers=["**/*.bats", "tests/bats/**"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=113,
        question=(
            "Do shell tests assume Bash 4+ features without skipping on Bash 3.2?"
        ),
        triggers=["**/*.bats"],
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    ),
    ChecklistItem(
        id=114,
        question=(
            "Are PII, secrets, or tokens logged, returned in errors, or exposed "
            "in API responses?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=115,
        question=(
            'Does a test assert field == "literal" where that literal is also '
            "defined in production code (wiring test that drifts silently)?"
        ),
        triggers=["**/test_*.py", "**/*_test.py", "**/tests/**/*.py"],
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=116,
        question=(
            "Does a non-strict schema (z.object) silently accept unexpected "
            "API fields?"
        ),
        triggers=["**/schemas/**", "**/*.{ts,tsx}"],
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=117,
        question=(
            "Are duplicate type definitions maintained separately from "
            "validation schemas (can drift undetected)?"
        ),
        triggers=["**/schemas/**", "**/types/**", "**/*.{ts,tsx}"],
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=118,
        question=(
            "Does CI verify two hand-maintained files agree instead of running "
            "a generator with --check?"
        ),
        triggers=[
            ".github/**/*.yml",
            ".github/**/*.yaml",
            "scripts/ci/**",
        ],
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=119,
        question=(
            "Does an automation hook (e.g. Renovate post-upgrade) silently fail "
            "while CI is supposed to catch drift—is the --check gate still present?"
        ),
        triggers=[".github/**/*.yml", "scripts/ci/**"],
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=120,
        question=(
            "Do bulk/planned-ahead tests assert signatures or data shapes instead "
            "of observable behavior?"
        ),
        triggers=list(_TEST_GLOBS),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=121,
        question=(
            "Would renaming an internal function break tests even though "
            "user-visible behavior is unchanged?"
        ),
        triggers=list(_TEST_GLOBS),
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=122,
        question=(
            "Are lint/security suppressions (noqa, type: ignore, allow(dead_code), "
            "# nosec) added without reviewing the underlying risk?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=123,
        question=(
            "Does fixture cleanup swallow delete/teardown errors (.catch(() => {})) "
            "hiding partial test pollution?"
        ),
        triggers=["**/fixtures/**", "**/*.{spec,test}.{ts,js}"],
        category=ReviewCategory.SILENT_FAILURE,
        tier=2,
    ),
    ChecklistItem(
        id=124,
        question=(
            "Is substantial shell logic inlined in workflow YAML instead of a "
            "dedicated script file?"
        ),
        triggers=[".github/workflows/**", ".github/actions/**"],
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=125,
        question=(
            "Are CI scripts referenced from workflows missing a shebang or "
            "executable bit?"
        ),
        triggers=["scripts/**/*.sh", ".github/workflows/**"],
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=126,
        question=(
            "Does HTTP request logic live in fixtures instead of a dedicated "
            "client layer?"
        ),
        triggers=["**/fixtures/**", "**/clients/**"],
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=127,
        question=(
            "Are API base paths duplicated across clients instead of centralized?"
        ),
        triggers=["**/clients/**", "**/*.{ts,js}"],
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=128,
        question=(
            "Do E2E tests share mutable global state across iterations "
            "(order-dependent failures)?"
        ),
        triggers=list(_E2E_TEST_GLOBS),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=129,
        question=(
            "Are browser contexts/fixtures left open without teardown "
            "(context.close())?"
        ),
        triggers=["**/fixtures/**", "**/*.{spec,test}.{ts,js}"],
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=130,
        question=(
            "Do shell tests leave temp files or env changes behind (missing teardown)?"
        ),
        triggers=["**/*.bats"],
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=131,
        question=(
            "Are pure functions unit-tested while the bug-prone call-site wiring "
            "has no integration coverage?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=132,
        question=(
            "Do tests hit the wrong layer (e.g. DB directly instead of the public "
            "API under test)?"
        ),
        triggers=list(_TEST_GLOBS),
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=133,
        question=(
            "Does new or changed production code lack corresponding test updates "
            "in the same diff?"
        ),
        triggers=["**/*.py", "**/*.rs", "**/*.{ts,tsx,js,jsx}"],
        category=ReviewCategory.INTEGRATION,
        tier=2,
    ),
    ChecklistItem(
        id=134,
        question=("Does new or changed logic lack tests covering the modified paths?"),
        triggers=["**/*.py", "**/*.rs", "**/*.{ts,tsx,js,jsx}", "scripts/**/*"],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=135,
        question=(
            "Are edge cases and failure modes (empty input, invalid input, "
            "missing files) untested?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=136,
        question=(
            "Are error-response tests missing or only checking one field per "
            "assertion (hiding partial regressions)?"
        ),
        triggers=["**/*.{spec,test}.{ts,js}"],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=137,
        question=(
            "Are security edge cases untested (injection payloads, malformed "
            "input, oversized payloads)?"
        ),
        triggers=[
            "**/*.{spec,test}.{ts,js,py}",
            "**/routes/**",
            "**/api/**",
        ],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=138,
        question=(
            "Are parameterized cases missing where multiple inputs share the "
            "same behavior rule?"
        ),
        triggers=["**/*.{spec,test}.{ts,js,py}", "**/*.bats"],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=139,
        question=(
            "Are tests non-deterministic (timing, network, shared state, random "
            "without seed)?"
        ),
        triggers=["**/*.{spec,test}.*", "**/*.bats"],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=140,
        question=(
            "Does the test name describe something different from what is "
            "actually asserted?"
        ),
        triggers=["**/*.{spec,test}.{ts,js}"],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=141,
        question=(
            "Are expected panics/failures tested with #[should_panic] or "
            "equivalent where invalid input must fail?"
        ),
        triggers=["**/*.rs", "**/tests/**"],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=142,
        question=(
            "Do exit-code paths in shell scripts lack assert_failure / status checks?"
        ),
        triggers=["**/*.bats", "scripts/**/*.sh"],
        category=ReviewCategory.TEST_GAP,
        tier=2,
    ),
    ChecklistItem(
        id=143,
        question=(
            "Do parametrize/test IDs embed mutable version or data values that "
            "will churn on every bump?"
        ),
        triggers=["**/test_*.py", "**/*_test.py"],
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=2,
    ),
    ChecklistItem(
        id=144,
        question=(
            "Are E2E/UI tests hard-coding labels, error strings, or IDs that "
            "should come from shared constants/enums?"
        ),
        triggers=["**/*.{spec,test}.{ts,js}", "**/enums/**"],
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=2,
    ),
    ChecklistItem(
        id=145,
        question=(
            "Does a public interface change lack updates to callers, schemas, "
            "or contract tests?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.CONTRACT_DRIFT,
        tier=2,
    ),
    ChecklistItem(
        id=146,
        question=(
            "Are credentials, API keys, tokens, or passwords hardcoded in source?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=147,
        question=(
            "Is user input interpolated into SQL/command strings instead of "
            "parameterized/safe APIs?"
        ),
        triggers=["**/*.py", "**/*.{ts,js,rs,go,java}"],
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=148,
        question=(
            "Is subprocess invoked with shell interpretation enabled "
            "(shell=True or equivalent)?"
        ),
        triggers=["**/*.py", "**/*.{sh,bash}", "scripts/**"],
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=149,
        question=(
            "Is input validated and sanitized at system boundaries (HTTP handlers, "
            "CLI args, file uploads)?"
        ),
        triggers=["**/api/**", "**/routes/**", "**/*.{py,ts,js,rs}"],
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=150,
        question=(
            "Are GitHub Actions referenced by mutable version tags instead of "
            "pinned commit SHAs?"
        ),
        triggers=[".github/workflows/**", ".github/actions/**"],
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=151,
        question=(
            "Does a # nosec / security suppression lack an inline justification "
            "on the flagged line?"
        ),
        triggers=["**/*.py"],
        category=ReviewCategory.SECURITY,
        tier=2,
    ),
    ChecklistItem(
        id=152,
        question=(
            "Does removing or renaming exported/public symbols lack migration "
            "or caller updates?"
        ),
        triggers=["**/*.{ts,tsx,js,py,rs}"],
        category=ReviewCategory.BREAKING_CHANGE,
        tier=2,
    ),
    ChecklistItem(
        id=153,
        question=(
            "Does a schema or API response change break strict contract tests "
            "or downstream parsers?"
        ),
        triggers=["**/schemas/**", "**/openapi/**", "**/api/**"],
        category=ReviewCategory.BREAKING_CHANGE,
        tier=2,
    ),
    ChecklistItem(
        id=154,
        question=(
            "Is type safety bypassed with any, unchecked casts, or broad "
            "suppressions on changed lines?"
        ),
        triggers=["**/*.{ts,tsx,js,jsx}", "**/*.py", "**/*.rs"],
        category=ReviewCategory.CODE_SMELL,
        tier=2,
    ),
    ChecklistItem(
        id=155,
        question=(
            "Do E2E tests rely on fragile CSS selectors (#id, deep div > button) "
            "likely to break on unrelated UI changes?"
        ),
        triggers=list(_E2E_TEST_GLOBS),
        category=ReviewCategory.CODE_SMELL,
        tier=2,
    ),
    ChecklistItem(
        id=156,
        question=(
            "Is the module interface nearly as complex as its implementation "
            "(shallow module)?"
        ),
        triggers=["**/*"],
        category=ReviewCategory.ARCHITECTURE,
        tier=2,
    ),
    ChecklistItem(
        id=157,
        question=(
            "Are tests written against implementation steps rather than "
            "user-/caller-visible behavior?"
        ),
        triggers=["**/*.{spec,test}.*"],
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
