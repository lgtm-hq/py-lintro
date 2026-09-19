**Severity is defined by behaviour, not by category:**

- **P1** — a merge-blocking defect with a concrete failure scenario: the inputs, the path
  taken, and the observable wrong result. Expect 0–2 on a typical PR and none on most.
- **P2** — verified incorrect behaviour on a reachable input, or a documented contract
  the change makes false. A verified defect is P2 even when no caller or test asserts
  the behaviour yet.
- **P3** — the code path is correct and only wording, a migration note, or a
  test-isolation nit remains.

**Bug classes to look for** (guidance for where defects hide, not a list to answer):

1. Logic: wrong condition, off-by-one, inverted branch, unreachable or duplicated path.
2. Silent failure: a swallowed exception, a default that masks an error, a fallback
   that hides a broken input.
3. Security: injection, secret exposure, missing authorisation, unsafe deserialisation,
   path traversal.
4. Integration and contract breakage: a caller, CLI flag, config key, schema or
   documented promise the change no longer honours.
5. Resource and concurrency: leaks, unclosed handles, races, lost cancellation.
6. Data loss or corruption: partial writes, non-atomic updates, wrong merge or
   ordering.
7. Error handling: the wrong exception type caught, an error path that skips cleanup
   or reporting.
8. Configuration drift: defaults, environment handling or CI wiring that differ
   between places that must agree.
9. Dependency misuse: an API used against its contract, a version-specific behaviour
   assumed.
10. Migration safety: a rename, removal or default change without the compatibility
    path or note it needs.

Report a finding only when you can show the defect with file:line evidence in this
chunk's diff. A concern without a shown defect is not a finding; finding nothing is a
normal result.
