#!/usr/bin/env bash
# assert_dispatch_allowed.sh
# Refuse a live (non-dry-run) direct workflow_dispatch of publish-npm.yml.
#
# npm trusted publishing (OIDC) matches on the *entry* workflow of the run, not
# on the reusable workflow doing the publishing. Every routine publish enters
# through the tag pipeline (publish-pypi-on-tag.yml, event `push` on refs/tags),
# which is the identity configured as the trusted publisher for the
# @lgtm-hq/lintro* packages. A direct dispatch of publish-npm.yml mints a token
# for `publish-npm.yml @ refs/heads/main`, which does not match: the OIDC
# exchange fails, npm falls back to an unauthenticated PUT, and the registry
# masks the authorization failure as `E404 Not Found` (see issue #2247).
#
# The authoritative discriminator is therefore the entry workflow, which
# GitHub exposes verbatim as `github.workflow_ref` (GITHUB_WORKFLOW_REF) — the
# same value that becomes the OIDC subject. `github.event_name` alone is not
# enough: a `workflow_call` run reports the *caller's* event, so dispatching
# the tag pipeline (which publish-npm.yml can legitimately be called from)
# would look identical to dispatching publish-npm.yml directly. The event is
# kept as a fail-closed fallback for the case where no workflow ref is
# available.
#
# Failing here — in a job that carries no `environment:` — keeps a doomed run
# from burning an `npm` environment approval and dying three retries deep on a
# misleading 404. Dry-run runs publish nothing and stay allowed.

set -euo pipefail

# Path fragment identifying this workflow inside GITHUB_WORKFLOW_REF, which
# looks like `<owner>/<repo>/.github/workflows/<file>@<ref>`.
readonly GUARDED_WORKFLOW='/.github/workflows/publish-npm.yml@'

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Refuse live workflow_dispatch runs of publish-npm.yml.

Usage: assert_dispatch_allowed.sh

Environment:
  WORKFLOW_REF  The run's entry workflow (github.workflow_ref), e.g.
                owner/repo/.github/workflows/publish-npm.yml@refs/heads/main.
                This is the OIDC subject npm matches on.
  EVENT_NAME    The triggering event (github.event_name). Only consulted when
                WORKFLOW_REF is empty.
  DRY_RUN       "true" when the run only performs `npm publish --dry-run`.
                Anything else is treated as a live publish.

Exits 0 when the run entered through the tag pipeline, and for any dry run;
exits 1 for a live direct dispatch, which cannot authenticate to npm.
EOF
	exit 0
fi

workflow_ref="${WORKFLOW_REF:-}"
event_name="${EVENT_NAME:-}"
dry_run="${DRY_RUN:-}"

if [[ "$dry_run" == "true" ]]; then
	echo "Dry run: nothing is published; proceeding."
	exit 0
fi

if [[ -n "$workflow_ref" ]]; then
	if [[ "$workflow_ref" != *"$GUARDED_WORKFLOW"* ]]; then
		echo "Entry workflow '$workflow_ref' is the trusted publisher identity; proceeding."
		exit 0
	fi
elif [[ "$event_name" != "workflow_dispatch" ]]; then
	# No workflow ref to inspect: fall back to the event. A `workflow_call`
	# run reports the caller's event, so anything but a dispatch entered
	# through a pipeline whose identity npm may trust.
	echo "Entry path '$event_name': npm trusted publishing applies; proceeding."
	exit 0
fi

cat >&2 <<'EOF'
ERROR: a live publish cannot be started from a direct dispatch of this workflow.

npm trusted publishing only authenticates runs whose entry workflow is the tag
pipeline (publish-pypi-on-tag.yml). A run that enters through publish-npm.yml
presents a different OIDC identity, so the registry rejects the publish and
reports it as a misleading `E404 Not Found`.

To publish (or backfill) a tag, approve / re-run the `Publish - PyPI Production`
(publish-pypi-on-tag.yml) run for that tag instead, and approve the `npm`
environment when that run reaches its waiting npm job.

Dispatching this workflow with dry_run enabled remains supported for testing.
EOF
if [[ -n "$workflow_ref" ]]; then
	echo "ERROR: this run's OIDC identity would be: $workflow_ref" >&2
fi
exit 1
