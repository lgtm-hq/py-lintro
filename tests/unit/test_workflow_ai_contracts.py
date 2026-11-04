"""Workflow wiring contracts: ai contracts."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import pytest
from assertpy import assert_that

from tests.unit.workflow_wiring_helpers import (
    _AI_CONTRACT_GATE_ENV,
    _AI_CONTRACT_TIER1_CONTEXT,
    _AI_CONTRACT_TIER2_JOB,
    _AI_CONTRACT_WORKFLOW,
    _AI_REVIEW_JOB,
    _AI_REVIEW_WORKFLOW,
    _API_SMOKE_JOB,
    _API_SMOKE_KEY,
    _API_SMOKE_WORKFLOW,
    _CHAIN_MEMBERSHIP_RE,
    _EGRESS_ENDPOINT_RE,
    _EXPRESSION_REFERENCE_RE,
    _GITHUB_PULL_REQUEST_EVENT,
    _LANE_SELECTOR_VARS,
    _REPO_ROOT,
    _ai_contract_tier1_job,
    _ai_contract_tier2_job,
    _api_smoke_matrix,
    _api_smoke_rows,
    _dogfood_provider_egress,
    _dogfood_review_step_env,
    _harden_runner_endpoints,
    _load_workflow,
    _mirrored_dogfood_env,
    _normalize_github_expr,
    _tier2_expression_from_dogfood,
    _vars_reads,
)


def test_ai_contract_tier1_is_required_check_safe() -> None:
    """Tier 1 must always report its context (#1119 / #1196).

    A ``paths:`` filter, or a job-level ``if:``, would stop the context from
    ever being created — which deadlocks the merge queue the moment the
    context is added to the ``checks-py-lintro`` ruleset. Tier 1 is cheap
    enough to run unconditionally (help probes only), so it stays a plain job
    with no path filter and no job-level ``if:``.
    """
    workflow = _load_workflow(name=_AI_CONTRACT_WORKFLOW)
    triggers = workflow["on"]

    assert_that(triggers).contains_key(_GITHUB_PULL_REQUEST_EVENT)
    assert_that(triggers).contains_key("merge_group")
    for event in (_GITHUB_PULL_REQUEST_EVENT, "merge_group"):
        assert_that(triggers[event] or {}).does_not_contain_key("paths")
        assert_that(triggers[event] or {}).does_not_contain_key("paths-ignore")
    assert_that(triggers["merge_group"]["types"]).contains("checks_requested")

    job = _ai_contract_tier1_job()
    assert_that(job["name"]).is_equal_to(_AI_CONTRACT_TIER1_CONTEXT)
    assert_that(job).does_not_contain_key("if")
    # A skipped reusable *caller* collapses its nested contexts, so the gate
    # must stay a plain job that always reports its own check run.
    assert_that(job).does_not_contain_key("uses")
    assert_that(job).contains_key("runs-on")

    # The README's admin PUT recipe is the third copy of the context string;
    # pin it to the constant so a job rename cannot leave the recipe stale.
    readme = (_REPO_ROOT / ".github" / "workflows" / "README.md").read_text(
        encoding="utf-8",
    )
    assert_that(readme).contains(_AI_CONTRACT_TIER1_CONTEXT)


def test_ai_contract_tier2_mirrors_dogfood_egress_and_gates_its_secrets() -> None:
    """Tier 2 must reach every dogfood lane's hosts, with gated credentials.

    Egress: the dogfood review allowlists provider hosts one lane at a time
    because a single run picks one provider. Tier 2 drives all three lanes in
    one job, so its allowlist must be a superset of that per-provider union —
    otherwise a lane that authenticates for the review dies here on blocked
    egress instead (#2481; the Codex subscription hosts are the case that
    reddened every lane).

    Secrets: Tier 2 runs on dispatch only today (#2600 removed the cron that
    reached it unwatched), but the round-trip work in #2515 adds a
    ``pull_request`` trigger. Every provider credential is
    therefore routed through one gate expression that also demands a
    same-repository head, so a fork PR resolves each secret to the empty
    string rather than reading it.
    """
    job = _ai_contract_tier2_job()

    dogfood = _dogfood_provider_egress()
    assert_that(dogfood).described_as("dogfood per-provider egress").is_not_empty()
    # Tier 2 takes the union of every declared lane's hosts from the lane
    # job, which derives it from the manifest with the dogfood resolver; no
    # provider host is written into this workflow (ADR-0011 A).
    tier2_endpoints = _harden_runner_endpoints(job=job)
    assert_that(tier2_endpoints).contains("needs.lane.outputs.egress-all")
    for endpoint in tier2_endpoints:
        if _EGRESS_ENDPOINT_RE.fullmatch(endpoint):
            assert_that(endpoint).described_as(endpoint).is_not_in(*sorted(dogfood))
    lane_job = _load_workflow(name=_AI_CONTRACT_WORKFLOW)["jobs"]["lane"]
    assert_that(lane_job["outputs"]["egress-all"]).is_equal_to(
        "${{ steps.resolve.outputs.egress_all }}",
    )
    assert_that(job["needs"]).contains("lane")

    # Exact, not substring: an added `|| github.event_name == 'push'` would
    # slip past independent contains() checks while widening what can read a
    # provider credential.
    gate = _normalize_github_expr(str(job["env"][_AI_CONTRACT_GATE_ENV]))
    assert_that(gate).is_equal_to(
        "${{ github.event_name == 'workflow_dispatch'"
        f" || github.event.{_GITHUB_PULL_REQUEST_EVENT}"
        ".head.repo.full_name == github.repository }}",
    )

    secret_env = {
        f"{step.get('name')} / {name}": _normalize_github_expr(str(value))
        for step in job["steps"]
        for name, value in (step.get("env") or {}).items()
        if "secrets." in str(value)
    }
    assert_that(secret_env).described_as(
        "Tier 2 must inject provider secrets",
    ).is_not_empty()
    for where, expression in secret_env.items():
        assert_that(expression).described_as(where).contains(
            f"env.{_AI_CONTRACT_GATE_ENV} == 'true'",
        )


def test_ai_contract_tier2_mirrors_every_dogfood_credential_and_cli_setting() -> None:
    """Tier 2 must carry the review's credentials and CLI settings, unchanged.

    A green Tier 2 is only evidence about the review if the two jobs drive the
    binaries the same way. Three shapes of mirror are checked, all derived from
    ai-review.yml rather than restated here, so a new dogfood env var fails
    this test until Tier 2 mirrors it or :data:`_DOGFOOD_ONLY_ENV` records why
    it should not:

    * The anthropic credential expressions select between the subscription
      token and the z.ai gateway on ``ZAI_BASE_URL`` (#2472 lane 2). Tier 2
      repeats the whole selection with one substitution — dogfood picks on its
      provider variable, Tier 2 (which always drives every lane) puts its
      trusted-event gate in that position.
    * The CLI-behaviour flags are plain literals — ``LINTRO_CLI_BARE: never``
      decides whether the anthropic lane proves an OAuth session or an API key
      — so they must match exactly.
    * Everything else carrying a secret or a variable must at least name the
      same one and ride the gate.
    """
    dogfood_env = _dogfood_review_step_env()
    tier2_env = next(
        step["env"]
        for step in _ai_contract_tier2_job()["steps"]
        if "run-ai-contract-tests.sh" in str(step.get("run", ""))
    )

    mirrored = _mirrored_dogfood_env(dogfood_env)
    assert_that(mirrored).described_as("derived dogfood mirror set").is_not_empty()

    for name, dogfood_value in mirrored.items():
        assert_that(tier2_env).described_as(
            f"Tier 2 must mirror the dogfood env var {name}",
        ).contains_key(name)
        tier2_value = _normalize_github_expr(str(tier2_env[name]))
        normalized = _normalize_github_expr(dogfood_value)

        if _CHAIN_MEMBERSHIP_RE.search(normalized):
            expected = _tier2_expression_from_dogfood(normalized)
            assert_that(tier2_value).described_as(name).is_equal_to(expected)
        elif "${{" not in normalized:
            assert_that(tier2_value).described_as(name).is_equal_to(normalized)
        else:
            for reference in _EXPRESSION_REFERENCE_RE.findall(normalized):
                assert_that(tier2_value).described_as(name).contains(reference)
            assert_that(tier2_value).described_as(name).contains(
                f"env.{_AI_CONTRACT_GATE_ENV} == 'true'",
            )


def test_tier2_binds_the_gateway_token_from_the_lane_row() -> None:
    """Tier 2 carries the active gateway's token as ANTHROPIC_AUTH_TOKEN.

    The dogfood wrapper binds ``ZAI_AUTH_TOKEN`` or ``KIMI_API_KEY`` to
    ``ANTHROPIC_AUTH_TOKEN`` for the attempt; Tier 2 has no wrapper, so its
    workflow binds the same secret to the same variable, one expression
    selecting by active lane. Both the token and the endpoint key on the
    *active* lane: a chain that lists the gateway as a fallback must not put
    its bearer token in front of the subscription CLI, which would send it
    to api.anthropic.com (ADR-0011 A, #2870).
    """
    tier2_env = next(
        step["env"]
        for step in _ai_contract_tier2_job()["steps"]
        if "run-ai-contract-tests.sh" in str(step.get("run", ""))
    )
    token = _normalize_github_expr(str(tier2_env["ANTHROPIC_AUTH_TOKEN"]))
    gate = f"env.{_AI_CONTRACT_GATE_ENV} == 'true'"
    assert_that(token).is_equal_to(
        f"${{{{ ({gate} && needs.lane.outputs.lane == 'zai')"
        " && secrets.ZAI_AUTH_TOKEN"
        f" || ({gate} && needs.lane.outputs.lane == 'kimi')"
        " && secrets.KIMI_API_KEY || '' }}",
    )
    base_url = _normalize_github_expr(str(tier2_env["ANTHROPIC_BASE_URL"]))
    assert_that(base_url).is_equal_to(
        f"${{{{ (env.{_AI_CONTRACT_GATE_ENV} == 'true'"
        " && needs.lane.outputs.first-party == 'false')"
        " && needs.lane.outputs.endpoint || '' }}",
    )
    assert_that(tier2_env).does_not_contain_key("LINTRO_AI_MODEL")


def test_the_structural_regexes_match_what_they_guard() -> None:
    """A regex that matches nothing guards nothing: pin each on a live sample."""
    dogfood_env = _dogfood_review_step_env()
    assert_that(
        _CHAIN_MEMBERSHIP_RE.search(dogfood_env["CLAUDE_CODE_OAUTH_TOKEN"]),
    ).is_not_none()
    assert_that(_vars_reads("x ${{ vars.ZAI_BASE_URL }} y")).is_equal_to(
        ["ZAI_BASE_URL"],
    )
    assert_that(_vars_reads('${{ vars["LINTRO_AI_MODEL"] }}')).is_equal_to(
        ["LINTRO_AI_MODEL"],
    )
    assert_that(_EGRESS_ENDPOINT_RE.fullmatch("api.anthropic.com:443")).is_not_none()


@pytest.mark.parametrize("workflow", [_AI_REVIEW_WORKFLOW, _AI_CONTRACT_WORKFLOW])
def test_no_dogfood_job_reads_a_routing_value_from_vars(workflow: str) -> None:
    """No variable may supply an endpoint or a transport (ADR-0011 A).

    The lane selector and the model override are variables, read by the
    resolver step of the secret-free lane job and nowhere else: the resolver
    accepts only a declared lane id and a model id, so the worst an inherited
    value can do is pick another declared row. Every ``vars`` read in the
    file, in any spelling GitHub accepts, is collected: the budget overlays
    may appear anywhere, the two selector names only in that step's ``env``,
    and nothing else at all.

    Args:
        workflow: The workflow file under test.
    """
    text = (_REPO_ROOT / ".github" / "workflows" / workflow).read_text(
        encoding="utf-8",
    )
    budget = {"LINTRO_AI_MAX_COST_USD", "LINTRO_AI_REVIEW_PR_BUDGET_USD"}
    selectors = {name.removeprefix("vars.") for name in _LANE_SELECTOR_VARS}
    # Only expressions read the context; prose in comments does not. A job or
    # step ``if:`` is an expression even without the ``${{ }}`` fence, so
    # every one of those is scanned as well.
    loaded = _load_workflow(name=workflow)
    conditions = [
        str(item.get("if", ""))
        for job in loaded["jobs"].values()
        for item in (job, *(job.get("steps") or []))
    ]
    expressions = " ".join(
        [*re.findall(r"\$\{\{(.*?)\}\}", text, re.S), *conditions],
    )
    reads = _vars_reads(expressions)
    assert_that(set(reads) - budget - selectors).described_as(
        f"{workflow} vars reads",
    ).is_empty()
    resolve = next(
        step for step in loaded["jobs"]["lane"]["steps"] if step.get("id") == "resolve"
    )
    in_place = [
        name
        for name in _vars_reads(" ".join(map(str, (resolve.get("env") or {}).values())))
        if name in selectors
    ]
    selector_reads = [name for name in reads if name in selectors]
    assert_that(set(selector_reads)).described_as(
        f"{workflow} selector reads",
    ).is_equal_to(selectors)
    assert_that(sorted(in_place)).described_as(
        f"{workflow}: every selector read sits in the lane job's resolve env",
    ).is_equal_to(sorted(selector_reads))
    # The collector sees every literal spelling, and any other use of the
    # context (``toJSON(vars)``, ``vars[expr]``) is refused outright, so a
    # banned name cannot hide behind a form the collector does not read.
    assert_that(_vars_reads("${{ vars['ZAI_BASE_URL'] }} ${{ vars.X }}")).is_equal_to(
        ["ZAI_BASE_URL", "X"],
    )
    assert_that(len(re.findall(r"\bvars\b", expressions))).described_as(
        f"{workflow}: every use of the vars context is a literal read",
    ).is_equal_to(len(reads))


def test_ai_review_job_has_no_lint_step() -> None:
    """Linter facts come from the untrusted lint job's artifact (#2571).

    The trusted review job holds the posting and provider credentials and
    checks out the base ref, so it must never run lintro's tools or any PR
    code: no lint step, no ``--with-lint``, and no second checkout.
    """
    review = _load_workflow(name=_AI_REVIEW_WORKFLOW)
    job = review["jobs"][_AI_REVIEW_JOB]
    checkouts = 0
    for step in job["steps"]:
        run = str(step.get("run", ""))
        assert_that(run).does_not_contain("--with-lint")
        assert_that(run).does_not_match(r"lintro\s+(chk|check|fmt|format)\b")
        assert_that(str(step.get("uses", ""))).does_not_contain("lgtm-ci/")
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            checkouts += 1
    assert_that(checkouts).is_equal_to(1)
    # `gh run download` of the report needs actions: read, which the job
    # already grants for review-state artifacts.
    assert_that(job["permissions"]["actions"]).is_equal_to("read")


def test_provider_api_smoke_runs_weekly_and_on_demand() -> None:
    """The live provider signal must be scheduled, since nothing else is.

    Tier 2 of the CLI contract suite gave up its cron in the same change, so
    this workflow carries the weekly cadence for every live provider check in
    the repo. A dispatch trigger stays alongside it so a fix can be proven
    without waiting a week.
    """
    triggers = _load_workflow(name=_API_SMOKE_WORKFLOW)["on"]
    assert_that(triggers).contains_key("schedule")
    assert_that(triggers).contains_key("workflow_dispatch")
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert_that(crons).is_length(1)
    # Weekly, exactly: a single day-of-week. A range or a list ('1-5', '1,3')
    # would pass a not-a-wildcard check while spending quota several times a
    # week, which is the cost this cadence was chosen to bound.
    day_of_week = crons[0].split()[4]
    assert_that(day_of_week).described_as(
        "the provider smoke must run on exactly one weekday",
    ).matches(r"^[0-6]$")


def test_cli_invocation_smoke_is_manual_only() -> None:
    """Tier 2 must not be reachable by a cron any more (#2600).

    Its credentials are subscription- and session-bound, so it cannot be kept
    green by design; running it unwatched produced a year's worth of red that
    meant nothing. Three places have to agree, or the demotion is cosmetic:
    the workflow declares no schedule, the job gates on dispatch alone, and
    the job name says so where a reader will see it.
    """
    workflow = _load_workflow(name=_AI_CONTRACT_WORKFLOW)
    assert_that(workflow["on"]).does_not_contain_key("schedule")

    job = workflow["jobs"][_AI_CONTRACT_TIER2_JOB]
    assert_that(_normalize_github_expr(str(job["if"]))).is_equal_to(
        "github.event_name == 'workflow_dispatch'",
    )
    assert_that(job["name"]).contains("manual only")

    # The docstring of the tier's own suite is where a contributor learns the
    # cadence; a stale "scheduled" there re-teaches the thing just removed.
    smoke_suite = (
        _REPO_ROOT / "tests" / "contract" / "test_cli_invocation_smoke.py"
    ).read_text(encoding="utf-8")
    assert_that(smoke_suite).contains("manual only")


def test_provider_api_smoke_is_driven_by_the_committed_table() -> None:
    """The matrix, the job name and the egress all come from the table.

    Adding a provider must cost a row and a secret. A matrix written into the
    workflow, or an allowlist that names hosts, would quietly reintroduce the
    workflow edit — and an unlisted host fails the call on blocked egress
    rather than on the provider's own verdict.
    """
    workflow = _load_workflow(name=_API_SMOKE_WORKFLOW)
    job = workflow["jobs"][_API_SMOKE_JOB]

    assert_that(str(job["strategy"]["matrix"])).contains(
        "needs.resolve-table.outputs.matrix",
    )
    assert_that(job["strategy"]["fail-fast"]).is_false()
    assert_that(str(job["name"])).contains("matrix.name")

    harden = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("step-security/")
    )
    allowlist = str(harden["with"]["allowed-endpoints"])
    assert_that(allowlist).described_as(
        "the provider host must come from the matrix row, not a literal list",
    ).contains("${{ matrix.egress }}")
    rows = _api_smoke_rows()
    assert_that(rows).is_not_empty()
    egress = {entry["name"]: entry["egress"] for entry in _api_smoke_matrix()}
    for row in rows:
        if "parked" in row:
            assert_that(egress).does_not_contain_key(row["name"])
            continue
        parsed = urlparse(row["base_url"])
        host = parsed.hostname
        # Without this, a row whose base_url does not parse yields host None
        # and the check below becomes the vacuous 'None:443' is absent — the
        # guard would pass on exactly the row that breaks egress.
        assert_that(host).described_as(
            f"{row['name']} base_url {row['base_url']!r} must parse to a host",
        ).is_not_none()
        # The allowlist entry is what the row is actually allowed to dial. A
        # row that reaches the matrix without one, or with one naming another
        # host or port, fails the weekly run on blocked egress with an opaque
        # network error instead of the provider's own verdict.
        assert_that(egress).described_as(
            "every active table row must reach the matrix with an egress entry",
        ).contains_key(row["name"])
        assert_that(egress[row["name"]]).described_as(
            f"{row['name']} egress must allow its own base_url host",
        ).is_equal_to(f"{host}:{parsed.port or 443}")
        assert_that(allowlist).described_as(
            f"{row['name']} host must not be hard-coded here",
        ).does_not_contain(f"{host}:443")

    resolve = workflow["jobs"]["resolve-table"]["steps"][-1]
    assert_that(str(resolve["run"])).contains("--emit-matrix")


def test_provider_api_smoke_skips_an_empty_matrix_before_expansion() -> None:
    """An all-parked table schedules no fake provider job or commit status."""
    jobs = _load_workflow(name=_API_SMOKE_WORKFLOW)["jobs"]
    assert_that(jobs["resolve-table"]["outputs"]["has_active"]).is_equal_to(
        "${{ steps.table.outputs.has_active }}",
    )
    assert_that(_normalize_github_expr(str(jobs[_API_SMOKE_JOB]["if"]))).is_equal_to(
        "needs.resolve-table.outputs.has_active == 'true'",
    )


def test_provider_api_smoke_failures_are_visible_on_main() -> None:
    """A failure must redden main and reach a human, not just this run.

    Two independent channels, because the single channel the CLI smoke had
    (the run list) is what failed for a month: a commit status on main's HEAD,
    and the deduplicated tracker issue. The status step is ``always()`` —
    reporting only on success is how a red job goes unseen.
    """
    workflow = _load_workflow(name=_API_SMOKE_WORKFLOW)
    job = workflow["jobs"][_API_SMOKE_JOB]

    assert_that(job["permissions"]["statuses"]).is_equal_to("write")
    status_step = next(
        step
        for step in job["steps"]
        if "report-commit-status.sh" in str(step.get("run", ""))
    )
    condition = _normalize_github_expr(str(status_step["if"]))
    assert_that(condition).contains("always()")
    assert_that(condition).contains("github.ref == 'refs/heads/main'")

    # What the status script *does* — the context it posts under and the
    # pending-not-green rule for a call that was never made — is asserted by
    # running it against a stubbed `gh` in
    # tests/scripts/test_ai_provider_smoke.py. Pinning its source text here
    # would break on a rename that changes no behaviour, and would equally be
    # satisfied by a comment. This test owns the wiring: that the job runs
    # that script at all, with the permission and the condition it needs.
    assert_that(status_step["env"]["SMOKE_OUTCOME"]).is_equal_to(
        "${{ steps.smoke.outputs.outcome }}",
    )
    assert_that(
        (_REPO_ROOT / "scripts" / "ci" / "ai_provider_smoke")
        .joinpath("report-commit-status.sh")
        .is_file(),
    ).is_true()

    # No job here may swallow its own verdict: the whole workflow exists to
    # make a failure arrive somewhere.
    for name, smoke_job in workflow["jobs"].items():
        assert_that(smoke_job).described_as(name).does_not_contain_key(
            "continue-on-error",
        )
        for step in smoke_job.get("steps", []):
            assert_that(step).described_as(
                f"{name}/{step.get('name')}",
            ).does_not_contain_key("continue-on-error")


def test_provider_api_smoke_files_a_distinctly_labelled_tracker_issue() -> None:
    """Failures reuse the shared filer, under their own label.

    Reusing ``reusable-main-failure-notifier.yml`` is what keeps one dedup
    marker mechanism in the repo rather than two. The label is what keeps a
    credit-exhaustion alarm distinguishable from a generic main failure at a
    glance, so it must not be the plain bug/ci pair every other caller uses.
    """
    workflow = _load_workflow(name=_API_SMOKE_WORKFLOW)
    notify = workflow["jobs"]["notify-failure"]

    assert_that(str(notify["uses"])).contains(
        "lgtm-hq/lgtm-ci/.github/workflows/reusable-main-failure-notifier.yml",
    )
    condition = _normalize_github_expr(str(notify["if"]))
    assert_that(condition).contains("failure()")
    assert_that(condition).contains("github.ref == 'refs/heads/main'")
    assert_that(notify["with"]["workflow-key"]).is_equal_to(_API_SMOKE_KEY)
    labels = str(notify["with"]["failure-issue-labels"]).split(",")
    assert_that(labels).contains("ai-provider-smoke")
    assert_that(notify["permissions"]["issues"]).is_equal_to("write")

    # The error text is what makes the issue actionable, and it reaches the
    # tracker through the notifier's own issue — never through a second filer.
    annotate = workflow["jobs"]["annotate-failure-issue"]
    assert_that(annotate["needs"]).contains("notify-failure")
    assert_that(annotate["permissions"]["issues"]).is_equal_to("write")
    comment_step = next(
        step
        for step in annotate["steps"]
        if "post_error_details.py" in str(step.get("run", ""))
    )
    assert_that(str(comment_step["run"])).contains("--workflow-key")
    details = (
        _REPO_ROOT / "scripts" / "ci" / "ai_provider_smoke" / "post_error_details.py"
    ).read_text(encoding="utf-8")
    assert_that(details).does_not_contain("issue create")
