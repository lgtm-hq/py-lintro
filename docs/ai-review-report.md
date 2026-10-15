# How to read a Lintro review report

The sticky PR comment is an index. Finding detail lives on inline comments.
Authoritative coverage state lives in workflow artifacts, not in the comment.

## Verdict

The title carries the derived verdict:

- **Blocked** — at least one open P1
- **Changes requested** — open P2, no P1
- **Nits only** — only open P3
- **Ready** — no open findings **and** every review-eligible file is covered at HEAD
- **Incomplete** — coverage-at-HEAD is below 100%. The findings-based label is withheld
  so a partial round can never look clean.

`~` on cost or tokens means the figure was estimated locally (subscription CLI, or a
provider that returned no usage counters). Subscription runs still show “what this would
have cost”; that figure is not a bill.

## Coverage and resume

A file is covered when its current normalized patch hash matches a stored entry.
Content-identical rebases keep coverage. The next round reviews never-reviewed files
first, then directly changed, then model-flagged, then group/import-invalidated files.

`--full` discards carried coverage for one run, and is also the only flag that forces a
round past the
[convergence stop rule](ai-features.md#review-convergence-deterministic-re-review-stop)
once it has fired — later pushes stay skipped until a `--full` run records a fresh
score. A skipped round's banner names any open P1 findings the last reviewed round left
behind; like a reviewed round's P1s, they do not redden the check.
`--max-cost-usd uncapped` lifts a flag/env cap. Overlay `0` is rejected; use `uncapped`
or a positive value.

When the per-PR question pass fails, every chunk is reviewed with the rubric alone. The
run log then says why, as one of `empty`, `not_json`, `not_list`, `no_question`,
`turn_limit` or `call_failed`, and quotes the redacted start of the model's answer when
one was received (for `turn_limit`, which leaves no answer, it gives the turn count
instead). `turn_limit` and `not_json` are retried once; the coverage degradation's
`detail` records the kind and whether a retry was made. The review is still complete, so
the failure is a note, not the "Coverage limited" warning, and the CI check passes with
a `::warning::`.

A rerun reaches the same verdict as the attempt it reruns, unless it redid the work that
attempt degraded. Each round saves its coverage degradations, with the step and the
files each one hit, in the review state (schema v6). The next round reads the latest
round's list, keyed like coverage by each file's patch hash rather than by the head: a
push that leaves a degraded file unchanged still owes its redo, and a file whose content
changed is reviewed on its own merits. A per-file reason whose files were still credited
(`output_exhaustion_retried`, `adversarial_sweep_failed`) sends those files back for
review. A redo that succeeds clears the reason. A redo that fails again, or that the
cost cap or PR budget stops, records the reason again and fails the check as the first
attempt did. A narrative reason whose step did not run again (a failed question pass on
a rerun with nothing left to review) is recorded again with the same warning.
`turn_limit_reached` and `split_half_failed` go through the same redo. Their files were
not credited in the failing round, so usually there is no coverage to set aside, but an
earlier round's coverage for one of those files is set aside too. A cut diff
(`diff_truncated`) is already re-reported by its coverage record until the file changes.

## Update in place

The primary sticky updates in place. When history would overflow GitHub’s comment cap,
an archive sticky is created and the primary keeps heading, aggregates, and a link.

A pull request whose sticky comment predates schema v2 (lintro before #1916,
August 2026) is not migrated. Its comment is still updated in place, but the run history
behind it starts again from round 1: v1 recorded run totals with no round numbers and no
per-finding identity, so there was nothing to carry forward that would not have been
guessed.
