# Fingerprint baselines

A fingerprint baseline lets a legacy project acknowledge existing findings while
failing checks on new findings. It is opt-in; without a CLI option or config entry,
Lintro keeps its existing behavior.

```bash
lintro baseline create . --tools ruff,mypy
lintro chk . --baseline .lintro-baseline.json
lintro baseline update . --tools ruff
lintro baseline prune . --tools ruff
lintro baseline show
```

Use `--baseline PATH` on these commands to choose a different file. Alternatively,
set `baseline: .lintro-baseline.json` in `.lintro-config.yaml` (or
`baseline = ".lintro-baseline.json"` under `[tool.lintro]`). CLI paths are relative
to the invocation directory; configured paths are relative to the project config.
Run from the project root: finding paths in the baseline are relative to that root.
Commit the baseline alongside the code. Creation refuses to overwrite a file.
`show` reads statistics without executing tools.

## Design for #438

The existing `.lintro/severity-baseline.json` is an automatic, scoped record of
severity **counts** used for comparison with the previous check. It does not
acknowledge individual findings and is not a suppression list. The new explicit
`.lintro-baseline.json` is separate. Fingerprint-filtered runs do not compare or
record severity history, so raw and filtered populations are never mixed.

Version 1 matches the tuple `(tool, relative file, rule code, SHA-256(message))`.
Line and column are excluded from identity; the stored line is only a hint.
Messages normalize CRLF to LF but preserve all other content, including numbers:
removing arbitrary numbers would also hide meaningful changes to a diagnostic.
Counts preserve multiplicity: acknowledging one occurrence cannot hide two.
Entries are sorted deterministically, contain no full messages, and can move
between clones of the same project. Paths outside the invocation root cannot be
acknowledged. File moves and changed messages or rule codes are new findings.

Filtering runs after execution, AI enhancement, and the format verify pass, before
final display or serialization. It filters detected and residual findings separately
and recomputes totals, severity counts, success, and measured fix accounting.
Baselined findings are reported separately, never counted as fixes. Timeout,
parse-failure, unknown residual, unknown inspection, and unexplained failure states
remain failures. Live diagnostic display is deferred when a baseline is active so
acknowledged findings cannot leak into the final report.

`update` adds currently observed findings and removes fixed entries only where
there is affirmative complete, exact file coverage. Unseen tools and files, unknown
coverage, skipped checks, execution failures, and incomplete reports retain their old
entries. `prune` only removes entries; it never acknowledges new findings. A
partial tool selection cannot discard the other tools' entries. Management scans
ignore configured suppression and do not invoke AI or install dependencies.

The boolean `inspected_files` proves that some work happened, not which files were
examined. Until #2609 supplies coverage throughout the tool registry, pruning must
not infer it from requested directories or the paths in diagnostics. The additive
`ToolResult.inspected_paths` contract names exact files a producer successfully
examined. The shared per-file check runner supplies completed file coverage; baseline
management additionally asks Ruff for its native `--show-files` list, respecting
native exclusions. Other producers retain entries until they supply exact coverage.
Ruff's lint discovery does not prove formatter coverage: its `FORMAT` entries are
retained even when lint coverage is exact. Producers may list such uncovered codes
in `uninspected_codes` to narrow their coverage claim.
A completed lint verdict with findings can remove other fixed entries within its
exact coverage; an execution failure cannot. Occurrence budgets shrink only over
that same coverage. Deleted files are retained because their absence alone is not
a successful inspection.

Baseline JSON is untrusted input: unknown keys/versions, duplicate keys or entries,
noncanonical/traversing/absolute paths, invalid hashes, invalid counts, and oversized
files fail closed before tools run. Writes use an exclusive sibling lock and an
atomic replacement; an error leaves the existing baseline intact. No rule packs
(#3023), provenance, or broader ignore system (#1610) are introduced here.
