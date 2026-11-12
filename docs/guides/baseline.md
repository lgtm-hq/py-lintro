# Fingerprint baselines

A fingerprint baseline lets a legacy project acknowledge existing findings while failing
checks on new findings. Filtering is opt-in; without a CLI option or config entry,
findings remain unfiltered. The stricter execution checks described below apply to all
runs.

```bash
lintro baseline create . --tools ruff,black
lintro chk . --tools ruff,black --baseline .lintro-baseline.json
lintro baseline update . --tools ruff
lintro baseline prune . --tools ruff
lintro baseline show
```

Use `--baseline PATH` on these commands to choose a different file. Alternatively, set
`baseline: .lintro-baseline.json` in `.lintro-config.yaml` (or
`baseline = ".lintro-baseline.json"` under `[tool.lintro]`). CLI paths are relative to
the invocation directory; configured paths are relative to the project config. This also
applies to a `baseline` value inherited from global user config: it resolves at the
project config directory, or at cwd when there is no project config, rather than at the
global config directory. A `baseline` value must be a non-empty string; an empty
`baseline:` (null) means no baseline, and any other value fails configuration loading
with a one-line error.

A `baseline` key in the global user config opts every project into filtering. A project
without that file fails `lintro check` with an error that names the global config file;
set `baseline` in the project config, remove it from the global config, or pass
`baseline=False` (Python API) or `--no-baseline` (watch).

Filtering and `create`, `update`, and `prune` require running from the project config
directory. Without a project config, a `baseline` value from the global config resolves
at the invocation directory, which is then its root, so a value such as `sub/x.json`
works from the directory that contains `sub`. Otherwise, without a project config, they
require running from the resolved baseline file's directory, including when using an
explicit `--baseline` path. Other invocation directories fail before tools run, with the
required root in the error. This prevents an acknowledgement for root `a.py` from hiding
a new finding in `pkg/a.py` when invoked from `pkg`. To check a subdirectory, run from
the root and pass `pkg` as the scan path. With a project config, baseline files may live
in a separate directory without changing the root. Finding paths are relative to this
root and case-sensitive on every platform; case-only renames change their identity.
Commit the baseline alongside the code. Use one baseline per project root: version one
does not record its root, so sharing a baseline between different project configurations
can alias relative file identities. Creation refuses to overwrite a file. `show` reads
statistics without executing tools and can run from any directory.

Treat the baseline as a reviewed suppression policy. Review additions, changed
fingerprints, and increased occurrence counts alongside code changes: `update` can
acknowledge new regressions, so do not run it automatically to make a failing CI check
pass. Use `prune` to remove proven-fixed acknowledgements without accepting new ones.
Keep a separate unfiltered check for tools outside the supported selection.

## Supported tools and scope

Certified **check** findings currently come from `ruff` and `black`. These producers
certify native lint verdicts and complete reports. A supported tool can still fail
inspection or parsing; those errors cannot be acknowledged.

Other producers, including `shellcheck`, `hadolint`, `shfmt`, `dotenv-linter`,
`pydoclint`, `sqlfluff`, `mypy`, `pylint`, and `bandit`, do not yet certify failed
findings for baselining. Their findings stay visible and failing under
`check --baseline`, even if the baseline contains matching entries. A successful, fully
inspected result with coherent findings can participate without explaining a failure,
but a nonzero native exit alone never proves completeness.

The shared per-file runner does not certify reports from sequence-only parsers: some
parsers discard malformed records without counting them. Surviving findings and even a
zero report-level failure count cannot prove that no records were lost. These findings
remain unsuppressed until a complete-report statistics contract is available. Rejected
ShellCheck reports retain their full native output for diagnosis; the additional error
explanation includes only a bounded excerpt.

Without `--tools`, management commands use the normal configured/default selection. If
**any active result** is incomplete or cannot certify its findings, `create`, `update`,
and `prune` abort the entire transaction and name the blocking tools and reasons. No
partial snapshot is written and no failing tool is silently dropped. For example,
`lintro baseline create . --tools ruff,mypy` aborts when mypy reports a type error, even
if Ruff's findings are complete. Use an explicit supported selection as above, and run
the remaining checks separately; their existing findings still need attention. Skipped
tools and tools with no matching files supply no coverage or acknowledgements; a scan
with no active tools also aborts.

This is a limited implementation of #438. A baseline across the full default tool set,
including the issue's mypy example, remains unsupported until those producers certify
their results. `fmt --baseline` additionally requires complete mutation and verification
results; unfixable residual findings may stay failing even for the check tools listed
above. The list is not a promise that every formatter can acknowledge residual findings.

## Design for #438

The existing `.lintro/severity-baseline.json` is an automatic, scoped record of severity
**counts** used for comparison with the previous check. It does not acknowledge
individual findings and is not a suppression list. The new explicit
`.lintro-baseline.json` is separate. Fingerprint-filtered runs do not compare or record
severity history, so raw and filtered populations are never mixed.

`lintro badge` always reports unfiltered severity counts. A configured fingerprint
baseline does not change this public quality claim, and a missing or invalid baseline
file does not prevent a badge scan. The Python API's `check`, `check_run`, `format`
(`fmt`), and `format_run` (`fmt_run`) accept `baseline=False` to explicitly disable
filtering, including loading the configured file. Omission or `baseline=None` uses the
project configuration; a string path overrides it. An unconfigured baseline file is
never loaded just because it exists. Callers publishing quality counts should opt out
explicitly, as the badge does.

The executor's `run_lint_tools_simple` also uses the configured baseline when its
`baseline` argument is omitted, and accepts the same `baseline=False` opt-out as the
public API. Watch mode passes `--no-baseline` through to it as `baseline=False`. Watch
validates the baseline once at startup and exits with an error, before watching, when
the file is missing or invalid; `--no-baseline` watches without it. Each run then
re-reads the file, so edits apply on the next batch.

Version 1 matches the tuple `(tool, relative file, rule code, SHA-256(message))`. Line
and column are excluded from identity; the stored line is only a hint. Messages
normalize CRLF to LF but preserve all other content, including numbers: removing
arbitrary numbers would also hide meaningful changes to a diagnostic. Counts preserve
multiplicity: acknowledging one occurrence cannot hide two. Entries are sorted
deterministically and contain no full messages. Relative finding paths allow reuse
between clones of the same project only when tool messages are stable: absolute paths,
clone-specific text, or changing numbers embedded in messages still change their hashes.
Paths outside the invocation root and findings exceeding the schema limits (including
paths longer than 4096 characters and rule codes longer than 1024 characters) cannot be
acknowledged and remain visible. File moves and changed messages or rule codes are new
findings.

Filtering runs after execution, AI enhancement, and the format verify pass, before final
display or serialization. The order is deliberate: AI fixes can change files, so the
filter must see the final findings, and an AI gate stays independent of
acknowledgements. Baselined findings are therefore still sent to the AI when AI is
enabled. It filters detected and residual findings separately and recomputes totals,
severity counts, success, and measured fix accounting. Baselined findings are reported
separately, never counted as fixes. Timeout, parse-failure, unknown residual, unknown
inspection, and unexplained failure states remain failures. A failed producer must
explicitly certify that its findings fully explain the failure before they can be
acknowledged. The supported check tools above supply this proof from their native exit
statuses and reports. Other failed producers retain their findings until they supply
that proof. Whenever findings are acknowledged, cached display text and native
diagnostic output are cleared, including when retained findings keep the result failing.
Final display and serialization use the retained structured findings. Incomplete results
preserve their findings and execution-error output. Live diagnostic display is deferred
when a baseline is active so final structured findings reflect filtering.

What a user sees for an incomplete native report: the result keeps every finding and its
native output, filtering is skipped for that tool, and when it has findings a warning
such as `Baseline not applied to ruff: report has 1 parse failure(s)` names the reason.
The run fails. A clean result from a producer that does not certify failed findings is
still complete: it has nothing to filter, so it passes with no warning and a baselined
count of zero.

Baselined counts appear in the JSON output (`baselined_count` per tool and
`summary.total_baselined`, in both `--output-format json` and JSON report files) and in
the console summary line `N issues (M baselined)`, which every stdout format except
JSON, SARIF, CSV, and Markdown prints. SARIF, CSV, and Markdown output, and report files
other than JSON, list only the retained findings and carry no baselined count.

**Source-parse diagnostics.** A finding that says the producer could not parse the
source file (Ruff's `invalid-syntax`; older Ruff releases used a null code or `E999`) is
never acknowledged: `create` and `update` do not record it, and filtering leaves it
visible even if a matching entry exists. Ruff skips other rules for a file it cannot
parse while still listing that file as inspected, so such a file also supplies no exact
coverage: `update` and `prune` keep every entry for it until it parses again. An
existing `invalid-syntax` entry in a baseline no longer acknowledges anything.

Format accounting matches fingerprint occurrences across the filtered initial and
residual lists, so line shifts do not cancel fix credit. If formatting introduces a new
residual identity or an extra occurrence, the initial count still describes only the
actual detected findings. The current result model requires
`initial = fixed + remaining` and has no introduced-finding counter, so such a result
uses explicit unknown accounting: `fixed` and `remaining` are null, both finding lists
are retained, the reason records matched fixes and introduced occurrences, and the run
fails. Unrepresentable identities also leave accounting unknown. Partial or already
unknown producer accounting is preserved without baseline suppression.

`update` adds currently observed findings and removes fixed entries only where there is
affirmative complete, exact file coverage. Unseen tools and files, unknown coverage,
skipped checks, execution failures, and incomplete reports retain their old entries.
`prune` only removes entries; it never acknowledges new findings. A partial tool
selection cannot discard the other tools' entries. User `--tool-options` overrides make
rule coverage unknown for each named tool, even when its file coverage is exact. For
those tools, `update` can add observations, but neither `update` nor `prune` removes old
entries or reduces occurrence budgets. Other tools retain their coverage claims.
Management scans ignore the configured fingerprint baseline and do not invoke AI or
install dependencies.

Native project configuration and configured tool options (including
`[tool.lintro.<tool>]`) still determine which rules are observed. Rule coverage is not
compared with previous scans. Disabling a rule there can let `update` or `prune` remove
its entries on fully covered files; re-enabling the rule then exposes those findings
again. This is a decided limit of version one, not an oversight: recording the active
rule set needs a schema change and a per-producer rule listing that no certified
producer supplies yet. Review removals in the baseline diff after changing rule
configuration.

The boolean `inspected_files` proves that some work happened, not which files were
examined. Until #2609 supplies coverage throughout the tool registry, pruning must not
infer it from requested directories or the paths in diagnostics. The additive
`ToolResult.inspected_paths` contract names exact files a producer successfully
examined. The shared per-file check runner leaves exact coverage unknown: native
exclusions in tools such as pydoclint, sqlfluff, and dotenv-linter can skip even
explicit file arguments and exit successfully. These tools can acknowledge observed
findings, but cannot remove old entries until they supply affirmative per-file
inspection proof. Baseline management asks Ruff for its native `--show-files` list,
respecting native exclusions. Both Ruff discovery and formatter reports must match
actual invocation identities, without duplicate identities. Multiline names, terminal
control characters, and ambiguous resolved names make coverage unknown for the
invocation. Ruff's lint discovery alone does not prove formatter coverage. When the
format check completes, management also requires the formatter's verbose per-file
execution records and matching completion counts. Coverage is the intersection of the
lint and formatter file sets. Disabled format checks, formatter exclusions, unknown
trace formats, and incomplete reports cannot remove `FORMAT` acknowledgements.

Black management scans request verbose output and use only affirmative per-file native
verdicts: would reformat, already formatted, or unchanged according to Black's cache.
Requested inputs, discovery messages, exclusion messages, and summary counts alone do
not authorize pruning. Black's separate E501 supplement does not yet supply exact file
coverage, so E501 entries are retained. Other producers retain entries until they supply
exact coverage. Producers list uncovered codes in `uninspected_codes` to narrow their
coverage claim. After a completed inspection proves a formatting finding fixed, `update`
and `prune` remove its acknowledgement; a later regression fails the check. An execution
failure cannot remove entries. Occurrence budgets shrink only over the same coverage.
Deleted files are retained because their absence alone is not a successful inspection.

Baseline JSON is untrusted input. Unknown schema versions, unexpected or duplicate JSON
members, repeated entries, non-canonical paths, parent traversal, absolute paths,
invalid hashes or counts, and oversized files fail closed before tools run. Writes use
an exclusive sibling lock and an atomic replacement; an error leaves the existing
baseline intact. `update` and `prune` take the lock before their scan and hold it until
the write, so another writer cannot change the file between the scan and the merge; a
concurrent management command fails with the lock error instead. After the replacement,
the writer also flushes the parent directory on POSIX so the new file survives a crash.
If the filesystem does not support a directory fsync (some network mounts), the write
still succeeds and a debug line is logged. Replacements preserve the existing file's
permission mode only: the replacement is owned by the user and group of the writing
process, not the previous owner. Creation uses ordinary file permissions restricted by
the process umask. Ctrl-C and errors release the lock, but a writer killed by a signal
such as SIGTERM, including a CI timeout, can leave a sibling `.lock` file. Confirm that
no baseline management command is active before removing that lock and retrying; locks
are never removed automatically by a later writer. No rule packs (#3023), provenance, or
broader ignore system (#1610) are introduced here.

## Execution checks for all runs

These fail-closed checks also apply without a fingerprint baseline:

- Ruff's lint check requires a structured report, including `[]` for a clean result.
  Empty stdout fails, and diagnostics without positive line and column locations count
  as parse failures rather than a complete report. A run that prints no report (empty
  stdout, or non-JSON output behind an exit other than 0 or 1) is logged as an execution
  or configuration error with Ruff's own message, not as unparsed diagnostic records.
- Bandit treats a run that could not execute, printed no usable JSON (including empty
  stdout at exit 0), listed files it could not scan, or reported a non-list `errors`
  container (one parse failure) as an execution failure. Inspection stays unknown, so
  such a run never updates a baseline. Files Bandit could not scan are named with its
  reason instead of the raw report. When no Python files match, the result is still "no
  files", not a failure.
- The shared per-file check runner accepts a nonzero exit as a complete lint verdict
  only when it is a declared findings exit status and has parsed findings. Other nonzero
  exits are execution errors even if some findings were parsed. This also applies to the
  fix runner's initial check; unknown execution is never a clean check.
- A format verification pass that cannot confirm inspection leaves `inspected_files`
  unknown. It supplies no affirmative inspection evidence for subsequent consumers.
