#!/usr/bin/env bash
# assert_dispatch_allowed.sh
# Allow a live npm publish only from the entry path npm trusts.
#
# npm trusted publishing (OIDC) matches on the *entry* workflow of the run, not
# on the reusable workflow doing the publishing. Every routine publish enters
# through the tag pipeline (publish-pypi-on-tag.yml), which is the identity
# configured as the trusted publisher for the @lgtm-hq/lintro* packages. A
# direct dispatch of publish-npm.yml mints a token for
# `publish-npm.yml @ refs/heads/main`, which does not match: the OIDC exchange
# fails, npm falls back to an unauthenticated PUT, and the registry masks the
# authorization failure as `E404 Not Found` (see issue #2247).
#
# The check is an ALLOWLIST, not a denylist of publish-npm.yml: exactly one
# entry workflow can authenticate, so anything else — a renamed workflow, a new
# caller, a run with no identity to inspect — is refused. A denylist would fail
# open the moment either workflow file is renamed.
#
# The discriminator is `github.workflow_ref` (GITHUB_WORKFLOW_REF), which is
# precisely the OIDC subject. `github.event_name` cannot substitute for it: a
# `workflow_call` run reports the *caller's* event, so a dispatched tag-pipeline
# run and a dispatched publish-npm.yml run look identical.
#
# Failing here — in a job that carries no `environment:` — keeps a doomed run
# from burning an `npm` environment approval and dying three retries deep on a
# misleading 404. Dry runs publish nothing and stay allowed.

set -euo pipefail

# The one entry workflow npm trusts, as it appears inside GITHUB_WORKFLOW_REF
# (`<owner>/<repo>/.github/workflows/<file>@<ref>`). Keep this in sync with the
# trusted publisher configured on npmjs; tests/unit/test_workflow_wiring.py
# asserts the named workflow exists and actually calls publish-npm.yml.
readonly TRUSTED_ENTRY_WORKFLOW='/.github/workflows/publish-pypi-on-tag.yml@'

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Allow an npm publish only from the trusted tag-pipeline entry path.

Usage: assert_dispatch_allowed.sh

Environment:
  WORKFLOW_REF  The run's entry workflow (github.workflow_ref), e.g.
                owner/repo/.github/workflows/publish-npm.yml@refs/heads/main.
                This is the OIDC subject npm matches on. Defaults to the
                runner's GITHUB_WORKFLOW_REF.
  DRY_RUN       "true" when the run only performs `npm publish --dry-run`.
                Anything else is treated as a live publish.

Exits 0 for any dry run, and for a live run whose entry workflow is
publish-pypi-on-tag.yml. Every other live run exits 1 — including a direct
dispatch of publish-npm.yml, which cannot authenticate to npm.
EOF
	exit 0
fi

# Fall back to the runner-provided value so a dropped `env:` mapping in the
# workflow still gates the publish instead of silently allowing it.
workflow_ref="${WORKFLOW_REF:-${GITHUB_WORKFLOW_REF:-}}"
dry_run="${DRY_RUN:-}"

if [[ "$dry_run" == "true" ]]; then
	echo "Dry run: nothing is published; proceeding."
	exit 0
fi

if [[ "$workflow_ref" == *"$TRUSTED_ENTRY_WORKFLOW"* ]]; then
	echo "Entry workflow '$workflow_ref' is the trusted publisher identity; proceeding."
	exit 0
fi

cat >&2 <<'EOF'
ERROR: a live publish can only run from the tag pipeline.

npm trusted publishing authenticates exactly one entry workflow:
publish-pypi-on-tag.yml. A run that enters through any other workflow — a
direct dispatch of publish-npm.yml, say — presents a different OIDC identity,
so the registry rejects the publish and reports it as a misleading
`E404 Not Found`.

To publish (or backfill) a tag, approve / re-run the `Publish - PyPI Production`
(publish-pypi-on-tag.yml) run for that tag instead, and approve the `npm`
environment when that run reaches its waiting npm job.

Dispatching this workflow with dry_run enabled remains supported for testing.
EOF
if [[ -n "$workflow_ref" ]]; then
	echo "ERROR: this run's OIDC identity would be: $workflow_ref" >&2
else
	echo "ERROR: no entry workflow (github.workflow_ref) to verify; refusing." >&2
fi
exit 1
