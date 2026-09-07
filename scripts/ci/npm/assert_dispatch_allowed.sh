#!/usr/bin/env bash
# assert_dispatch_allowed.sh
# Refuse a live (non-dry-run) direct workflow_dispatch of publish-npm.yml.
#
# npm trusted publishing (OIDC) matches on the *entry* workflow of the run, not
# on the reusable workflow doing the publishing. Every routine publish enters
# through the tag pipeline (publish-pypi-on-tag.yml @ refs/tags/v*, event
# `push`), which is the identity configured as the trusted publisher for the
# @lgtm-hq/lintro* packages. A direct dispatch of publish-npm.yml mints a token
# for `publish-npm.yml @ refs/heads/main` (event `workflow_dispatch`), which
# does not match: the OIDC exchange fails, npm falls back to an unauthenticated
# PUT, and the registry masks the authorization failure as `E404 Not Found`
# (see issue #2247). No `workflow_dispatch` entry path carries the trusted
# identity, so the event alone is the discriminator.
#
# Failing here — in a job that carries no `environment:` — keeps a doomed run
# from burning an `npm` environment approval and dying three retries deep on a
# misleading 404. Dry-run dispatches publish nothing and stay allowed.

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Refuse live workflow_dispatch runs of publish-npm.yml.

Usage: assert_dispatch_allowed.sh

Environment:
  EVENT_NAME   The triggering event (github.event_name).
  DRY_RUN      "true" when the run only performs `npm publish --dry-run`.
               Anything else is treated as a live publish.

Exits 0 for the tag-pipeline `workflow_call` path and for dry-run dispatches;
exits 1 for a live dispatch, which cannot authenticate to npm.
EOF
	exit 0
fi

event_name="${EVENT_NAME:-}"
dry_run="${DRY_RUN:-}"

if [[ "$event_name" != "workflow_dispatch" ]]; then
	echo "Entry path '$event_name': npm trusted publishing applies; proceeding."
	exit 0
fi

if [[ "$dry_run" == "true" ]]; then
	echo "Dry-run dispatch: nothing is published; proceeding."
	exit 0
fi

cat >&2 <<'EOF'
ERROR: a live publish cannot be started from a direct dispatch of this workflow.

npm trusted publishing only authenticates runs that enter through the tag
pipeline (publish-pypi-on-tag.yml @ refs/tags/v*). A dispatched run presents a
different OIDC identity, so the registry rejects the publish and reports it as a
misleading `E404 Not Found`.

To publish (or backfill) a tag, approve / re-run the `Publish - PyPI Production`
(publish-pypi-on-tag.yml) run for that tag instead, and approve the `npm`
environment when that run reaches its waiting npm job.

Dispatching this workflow with dry_run enabled remains supported for testing.
EOF
if [[ -n "${GITHUB_WORKFLOW_REF:-}" ]]; then
	echo "ERROR: this run's OIDC identity would be: $GITHUB_WORKFLOW_REF" >&2
fi
exit 1
