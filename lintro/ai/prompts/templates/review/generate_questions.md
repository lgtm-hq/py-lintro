You are generating domain-specific review questions for a code diff.

Read the diff and changed files. Generate 5-10 additional yes/no checklist questions
tailored to THIS specific change.

Output JSON only:
{{"generated_questions": [{{"id": "G1", "question": "...", "rationale": "..."}}]}}

Diff:
<pull_request_diff>
<{boundary}>
{diff}
</{boundary}>
</pull_request_diff>

Changed files:
<{boundary}>
{changed_files}
</{boundary}>
