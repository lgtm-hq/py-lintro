# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""The review-lanes manifest is validated, not trusted (ADR-0011 A, #2869).

The committed manifest resolves; every refusal the ADR lists fails closed and
names the declared lanes; the outputs are single-line step outputs; and the
manifest's own claims about lintro (which models carry a list-price table)
are pinned to the package. Both lane jobs (``ai-review.yml`` and
``ai-contract-tests.yml``) invoke the resolver secret-free (#2870).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess  # nosec B404 - runs the repo's own script with a fixed argv
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from assertpy import assert_that

from lintro.ai.registry import model_pricing

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "resolve_review_lanes.py"
MANIFEST = ROOT / "scripts" / "ci" / "ai_review_lanes.json"
WORKFLOWS = ROOT / ".github" / "workflows"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("resolve_review_lanes", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def resolver() -> ModuleType:
    """Return the resolver module, loaded by path like the workflow would run it."""
    return _load()


@pytest.fixture
def manifest() -> dict[str, Any]:
    """Return a fresh copy of the committed manifest to mutate."""
    return copy.deepcopy(json.loads(MANIFEST.read_text(encoding="utf-8")))


def _resolve(resolver: ModuleType, document: dict[str, Any]) -> Any:
    return resolver.resolve_chain(document)


def _refuses(resolver: ModuleType, document: dict[str, Any], *fragments: str) -> None:
    with pytest.raises(resolver.LaneError) as caught:
        resolver.resolve_chain(document)
    message = str(caught.value)
    for fragment in fragments:
        assert_that(message).contains(fragment)
    # Every refusal names what the manifest declares (ADR-0011 A).
    assert_that(message).contains("declared lanes:")


# --- the committed manifest ------------------------------------------------------


def test_the_committed_manifest_resolves_to_the_claude_subscription_lane(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """The initial chain is the sole first-party Claude lane (ADR-0011 A)."""
    resolved = _resolve(resolver, manifest)

    assert_that(resolved.chain).is_equal_to(["claude-subscription"])
    assert_that(resolved.active["endpoint"]).is_equal_to("https://api.anthropic.com")
    assert_that(resolved.active["first_party"]).is_true()
    assert_that(resolved.egress).is_equal_to(["api.anthropic.com:443"])


def test_the_committed_manifest_declares_every_initial_lane(
    manifest: dict[str, Any],
) -> None:
    """The four rows the ADR names are present; z.ai is declared, not active."""
    assert_that(set(manifest["lanes"])).is_equal_to(
        {"claude-subscription", "codex-subscription", "cursor", "zai-glm-gateway"},
    )
    zai = manifest["lanes"]["zai-glm-gateway"]
    assert_that(zai["first_party"]).is_false()
    # The secret is one thing, the variable the CLI reads another (#2899).
    assert_that(zai["credential_secret"]).is_equal_to("ZAI_AUTH_TOKEN")
    assert_that(zai["credential_env"]).is_equal_to("ANTHROPIC_AUTH_TOKEN")
    assert_that(zai["egress"]).is_equal_to(["api.z.ai:443"])
    assert_that(manifest["chain"]).does_not_contain("zai-glm-gateway")


def test_every_declared_lane_passes_the_row_checks_on_its_own(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """Each row is valid as a single-entry chain, so switching is a one-line diff."""
    for lane_id in manifest["lanes"]:
        document = copy.deepcopy(manifest)
        document["chain"] = [lane_id]

        resolved = _resolve(resolver, document)

        assert_that(resolved.chain).is_equal_to([lane_id])


def test_the_manifests_priced_models_match_lintros_list_price_tables(
    manifest: dict[str, Any],
) -> None:
    """The resolver is stdlib-only, so the manifest carries this fact; pin it."""
    priced = {
        model
        for model, pricing in model_pricing().items()
        if pricing.input_per_million > 0 and pricing.output_per_million > 0
    }

    assert_that(set(manifest["priced_models"])).is_equal_to(priced)


def test_the_manifest_binds_no_secret_value(manifest: dict[str, Any]) -> None:
    """Credential fields name variables and files, never values."""
    for row in manifest["lanes"].values():
        assert_that(row["credential_secret"]).matches(r"^[A-Z][A-Z0-9_]+$")
        for field in ("credential_env", "credential_source_env", "base_url_env"):
            value = row.get(field)
            if value is not None:
                assert_that(value).matches(r"^[A-Z][A-Z0-9_]+$")


def _lane_job(workflow: str) -> dict[str, Any]:
    loaded = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    job = loaded["jobs"]["lane"]
    assert isinstance(job, dict)
    return job


@pytest.mark.parametrize("workflow", ["ai-review.yml", "ai-contract-tests.yml"])
def test_the_lane_job_runs_the_resolver_secret_free(workflow: str) -> None:
    """Both consumers resolve the chain in a job that holds no secret (#2870).

    The review workflow reads the manifest at ``github.workflow_sha``, the
    default-branch commit that supplied the definition (ADR-0011 A); the
    contract-tests workflow reads it from the dispatched ref.

    Args:
        workflow: The workflow file under test.
    """
    job = _lane_job(workflow)
    blob = json.dumps(job)
    assert_that(blob).does_not_contain("secrets.")
    assert_that(job["permissions"]).is_equal_to({"contents": "read"})
    checkout = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    sparse = str(checkout["with"]["sparse-checkout"]).split()
    assert_that(sparse).is_equal_to(
        ["scripts/ci/ai_review_lanes.json", "scripts/ci/resolve_review_lanes.py"],
    )
    assert_that(checkout["with"]["persist-credentials"]).is_false()
    if workflow == "ai-review.yml":
        assert_that(checkout["with"]["ref"]).is_equal_to("${{ github.workflow_sha }}")
        # A comment that cannot be a review request spends no runner.
        condition = " ".join(str(job["if"]).split())
        assert_that(condition).contains("github.event_name == 'pull_request_target'")
        assert_that(condition).contains(
            "startsWith(github.event.comment.body, '@lintro review')",
        )
    resolve = next(step for step in job["steps"] if step.get("id") == "resolve")
    assert_that(str(resolve["run"]).strip()).is_equal_to(
        "python3 -I scripts/ci/resolve_review_lanes.py --format github",
    )
    for output in ("chain", "lane"):
        assert_that(job["outputs"][output]).is_equal_to(
            f"${{{{ steps.resolve.outputs.{output} }}}}",
        )


def test_first_party_must_agree_with_the_endpoint(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """A gateway cannot be declared first-party, nor the provider's host a gateway."""
    pristine = copy.deepcopy(manifest)
    claude = manifest["lanes"]["claude-subscription"]
    claude["endpoint"] = "https://api.z.ai/api/anthropic"
    claude["egress"] = ["api.z.ai:443"]
    _refuses(resolver, manifest, "first_party must be False")
    manifest = pristine
    zai = manifest["lanes"]["zai-glm-gateway"]
    zai["endpoint"] = "https://api.anthropic.com"
    zai["egress"] = ["api.anthropic.com:443"]
    _refuses(resolver, manifest, "first_party must be True")


def test_a_gateway_row_carries_an_unknown_basis_and_a_credential_its_form(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """Decision B fixes the gateway basis; a credential kind fixes its form."""
    pristine = copy.deepcopy(manifest)
    manifest["lanes"]["zai-glm-gateway"]["cost_basis"] = "billed"
    _refuses(resolver, manifest, "cost basis must be 'unknown'")
    manifest = copy.deepcopy(pristine)
    codex = manifest["lanes"]["codex-subscription"]
    codex["credential_form"] = "env"
    codex["credential_env"] = "CODEX_AUTH_JSON"
    _refuses(resolver, manifest, "takes the 'file' form")
    manifest = copy.deepcopy(pristine)
    manifest["lanes"]["codex-subscription"]["credential_env"] = "CODEX_AUTH_JSON"
    _refuses(resolver, manifest, "carries ['credential_env']")
    manifest = copy.deepcopy(pristine)
    manifest["lanes"]["cursor"]["credential_file"] = "$HOME/x"
    _refuses(resolver, manifest, "carries ['credential_file']")


def test_duplicate_and_unknown_keys_are_refused(
    resolver: ModuleType,
    manifest: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A repeated lane or a stray key is a mistake a reviewer must see."""
    manifest["default"] = "cursor"
    _refuses(resolver, manifest, "unknown keys ['default']")
    # The message is built before ``lanes`` is checked, so a malformed one
    # must not turn the refusal into a traceback.
    malformed = copy.deepcopy(manifest)
    malformed["lanes"] = 5
    _refuses(resolver, malformed, "unknown keys ['default']", "(none)")
    manifest = copy.deepcopy(manifest)
    del manifest["default"]
    manifest["lanes"]["cursor"]["selector"] = True
    _refuses(resolver, manifest, "unknown fields ['selector']")
    duplicated = tmp_path / "lanes.json"
    text = MANIFEST.read_text(encoding="utf-8")
    lanes_at = text.index('"lanes": {')
    duplicated.write_text(
        text[: lanes_at + len('"lanes": {')]
        + '\n    "cursor": {},'
        + text[lanes_at + len('"lanes": {') :],
        encoding="utf-8",
    )
    with pytest.raises(resolver.LaneError) as caught:
        resolver.load_manifest(duplicated)
    assert_that(str(caught.value)).contains("duplicate key 'cursor'")


def test_egress_hosts_are_compared_case_insensitively_and_ports_are_ascii(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """An upper-case host is the same host; a Unicode digit is not a port."""
    row = manifest["lanes"]["claude-subscription"]
    row["egress"] = ["API.anthropic.com:443"]
    assert_that(_resolve(resolver, manifest).egress).is_equal_to(
        ["api.anthropic.com:443"],
    )
    row["egress"] = ["api.anthropic.com:\u00b2"]
    _refuses(resolver, manifest, "host:port")
    for host in ("evil,x.com", "a..b", "-a.example.com", "a-.example.com", "a_b.com"):
        row["egress"] = [f"{host}:443", "api.anthropic.com:443"]
        _refuses(resolver, manifest, "is not a hostname")
    row["egress"] = ["api.anthropic.com:443"]
    row["endpoint"] = "https://[api.anthropic.com/"
    _refuses(resolver, manifest, "not a valid URL")


# --- fail-closed paths -------------------------------------------------------------


def test_an_empty_or_undeclared_chain_is_refused(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """The chain must name declared rows."""
    manifest["chain"] = []
    _refuses(resolver, manifest, "non-empty")
    manifest["chain"] = ["no-such-lane"]
    _refuses(resolver, manifest, "undeclared lane 'no-such-lane'")
    manifest["chain"] = ["claude-subscription", "claude-subscription"]
    _refuses(resolver, manifest, "repeats a lane")


def test_a_chain_cannot_mix_first_party_values_or_fall_back_from_a_gateway(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """Decision D: one job holds every chain credential, so first_party must agree."""
    manifest["chain"] = ["claude-subscription", "zai-glm-gateway"]
    _refuses(resolver, manifest, "mixes first-party")
    manifest["lanes"]["zai-twin"] = copy.deepcopy(manifest["lanes"]["zai-glm-gateway"])
    manifest["chain"] = ["zai-glm-gateway", "zai-twin"]
    _refuses(resolver, manifest, "single-entry chain")


def test_a_non_https_endpoint_is_refused(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """A cleartext endpoint would carry the credential in a header over http."""
    manifest["lanes"]["claude-subscription"]["endpoint"] = "http://api.anthropic.com"
    _refuses(resolver, manifest, "https")


def test_an_endpoint_host_missing_from_its_own_egress_is_refused(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """The lane could not reach the host it names."""
    manifest["lanes"]["claude-subscription"]["egress"] = ["api.z.ai:443"]
    _refuses(resolver, manifest, "missing from its egress")


def test_a_wildcard_or_malformed_egress_host_is_refused(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """No lane may declare a wildcard host, and every port must be a real one."""
    row = manifest["lanes"]["claude-subscription"]
    row["egress"] = ["*.anthropic.com:443", "api.anthropic.com:443"]
    _refuses(resolver, manifest, "wildcard")
    row["egress"] = ["api.anthropic.com"]
    _refuses(resolver, manifest, "host:port")
    row["egress"] = ["api.anthropic.com:99999"]
    _refuses(resolver, manifest, "host:port")


def test_the_endpoint_port_must_be_in_the_lanes_egress(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """An allowed host on another port cannot reach the endpoint."""
    row = manifest["lanes"]["claude-subscription"]
    row["egress"] = ["api.anthropic.com:444"]
    _refuses(resolver, manifest, "api.anthropic.com:443 is missing")
    row["egress"] = ["api.anthropic.com:443"]
    row["endpoint"] = "https://api.anthropic.com:8443"
    _refuses(resolver, manifest, "api.anthropic.com:8443 is missing")
    row["egress"] = ["api.anthropic.com:8443"]
    assert_that(_resolve(resolver, manifest).egress).is_equal_to(
        ["api.anthropic.com:8443"],
    )
    # An explicit port zero is not "the default port".
    row["endpoint"] = "https://api.anthropic.com:0"
    _refuses(resolver, manifest, "port is out of range")
    row["endpoint"] = "https://api.anthropic.com:443"
    row["egress"] = ["api.anthropic.com:443"]
    assert_that(_resolve(resolver, manifest).chain).is_equal_to(["claude-subscription"])


def test_free_form_fields_carry_no_whitespace_or_control_character(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """Every published string is read by a shell; none may carry a break or space."""
    for field, value in (
        ("endpoint", "https://api.anthropic.com/\n"),
        ("model", "claude-opus-5-5 x"),
        ("shadow_price_model", "claude-opus-4-20250514\t"),
    ):
        bad = copy.deepcopy(manifest)
        bad["lanes"]["claude-subscription"][field] = value
        _refuses(resolver, bad, "no whitespace or control character")
    bad = copy.deepcopy(manifest)
    bad["lanes"]["claude-subscription"]["models"].append("claude\rsonnet")
    _refuses(resolver, bad, "no whitespace or control character")
    bad = copy.deepcopy(manifest)
    bad["lanes"]["codex-subscription"]["base_url_files"][0]["key"] = "chatgpt base_url"
    _refuses(resolver, bad, "no whitespace or control character")


def test_a_lane_id_or_variable_name_cannot_carry_a_second_output_line(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """Ids and names become step outputs; a line break in one is refused."""
    hostile = copy.deepcopy(manifest)
    hostile["lanes"]["claude-subscription\nprovider=cursor"] = hostile["lanes"].pop(
        "claude-subscription",
    )
    hostile["chain"] = ["claude-subscription\nprovider=cursor"]
    _refuses(resolver, hostile, "lane ids must be")
    for field in ("credential_env", "credential_secret", "base_url_env"):
        bad = copy.deepcopy(manifest)
        bad["lanes"]["claude-subscription"][field] = "ANTHROPIC_BASE_URL\nX=y"
        _refuses(resolver, bad, "must be a variable name")
    # A trailing line break is not an id either (``$`` would have let it by).
    trailing = copy.deepcopy(manifest)
    trailing["lanes"]["claude-subscription\n"] = trailing["lanes"].pop(
        "claude-subscription",
    )
    trailing["chain"] = ["claude-subscription\n"]
    _refuses(resolver, trailing, "lane ids must be")
    trailing = copy.deepcopy(manifest)
    trailing["lanes"]["claude-subscription"][
        "credential_env"
    ] = "CLAUDE_CODE_OAUTH_TOKEN\n"
    _refuses(resolver, trailing, "must be a variable name")
    # The writer refuses a multi-line value even if one were ever built.
    with pytest.raises(resolver.LaneError):
        resolver._write_outputs({"lane": "a\nb"})


def test_a_model_outside_the_allowed_list_is_refused(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """The default must be one of the models the lane allows."""
    manifest["lanes"]["claude-subscription"]["model"] = "claude-haiku-4-5-20251001"
    _refuses(resolver, manifest, "outside its allowed models")


def test_the_shadow_pricing_rule_fails_closed(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """Decision B: a gateway row, or an unpriced model, needs a shadow table."""
    zai = manifest["lanes"]["zai-glm-gateway"]
    del zai["shadow_price_model"]
    _refuses(resolver, manifest, "not first-party", "shadow_price_model")
    manifest = copy.deepcopy(manifest)
    manifest["lanes"]["zai-glm-gateway"]["shadow_price_model"] = "claude-sonnet-4-6"
    claude = manifest["lanes"]["claude-subscription"]
    del claude["shadow_price_model"]
    _refuses(resolver, manifest, "unpriced models", "claude-opus-5-5")
    claude["shadow_price_model"] = "no-such-table"
    _refuses(resolver, manifest, "has no list-price table")


def test_a_row_with_a_missing_or_mistyped_field_is_refused(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """The schema is checked field by field."""
    missing = copy.deepcopy(manifest)
    del missing["lanes"]["cursor"]["egress"]
    _refuses(resolver, missing, "missing 'egress'")
    mistyped = copy.deepcopy(manifest)
    mistyped["lanes"]["cursor"]["first_party"] = "yes"
    _refuses(resolver, mistyped, "wrong type")
    unbound = copy.deepcopy(manifest)
    del unbound["lanes"]["cursor"]["credential_env"]
    _refuses(resolver, unbound, "without 'credential_env'")
    unknown_basis = copy.deepcopy(manifest)
    unknown_basis["lanes"]["codex-subscription"]["cost_basis"] = "free"
    _refuses(resolver, unknown_basis, "unknown cost basis")


def test_a_lane_that_reports_model_usage_must_name_a_model(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """Verification needs a requested model to compare the envelope against."""
    row = manifest["lanes"]["claude-subscription"]
    row["model"] = None
    row["models"] = []
    _refuses(resolver, manifest, "names no model to verify")


# --- outputs -------------------------------------------------------------------------


def test_outputs_are_single_line_and_carry_the_whole_chain(
    resolver: ModuleType,
    manifest: dict[str, Any],
) -> None:
    """Every output is one ``name=value`` line; lists and rows travel as JSON."""
    manifest["chain"] = ["claude-subscription", "codex-subscription"]

    values = resolver.outputs_for(_resolve(resolver, manifest))

    assert_that(values["lane"]).is_equal_to("claude-subscription")
    assert_that(json.loads(values["chain"])).is_equal_to(
        ["claude-subscription", "codex-subscription"],
    )
    assert_that(values["egress"]).is_equal_to(
        "api.anthropic.com:443 api.openai.com:443 auth.openai.com:443 chatgpt.com:443",
    )
    # Every declared row's hosts, for the job that drives every lane at once.
    assert_that(values["egress_all"].split()).contains(
        "api.anthropic.com:443",
        "api.cursor.com:443",
        "api.z.ai:443",
    )
    assert_that(set(json.loads(values["lanes"]))).is_equal_to(
        {"claude-subscription", "codex-subscription"},
    )
    assert_that(values["first_party"]).is_equal_to("true")
    assert_that(values["base_url_env"]).is_equal_to("ANTHROPIC_BASE_URL")
    assert_that(json.loads(values["models"])).contains("claude-opus-5-5")
    assert_that(values["credential_file"]).is_equal_to("")
    for value in values.values():
        assert_that(value).does_not_contain("\n")


def test_the_script_writes_github_outputs_and_fails_closed_with_an_error_line(
    tmp_path: Path,
    manifest: dict[str, Any],
) -> None:
    """Run as the workflow would: outputs to GITHUB_OUTPUT, refusals to stderr."""
    output = tmp_path / "out.txt"
    env = {"GITHUB_OUTPUT": str(output), "PATH": "/usr/bin:/bin"}

    ok = subprocess.run(  # nosec B603 - fixed argv, the repo's own script
        [sys.executable, "-I", str(SCRIPT), "--manifest", str(MANIFEST)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(ok.returncode).is_equal_to(0)
    assert_that(output.read_text(encoding="utf-8")).contains(
        "lane=claude-subscription\n",
    )
    assert_that(ok.stderr).contains("active 'claude-subscription'")

    manifest["chain"] = ["no-such-lane"]
    broken = tmp_path / "lanes.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")

    refused = subprocess.run(  # nosec B603 - fixed argv, the repo's own script
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--manifest",
            str(broken),
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(refused.returncode).is_equal_to(1)
    assert_that(refused.stderr).contains("::error::review lanes:")
    assert_that(refused.stderr).contains("declared lanes: claude-subscription")
    assert_that(refused.stdout).is_empty()
