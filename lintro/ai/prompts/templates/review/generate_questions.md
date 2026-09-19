You are generating review questions for one pull request. Read the PR title, the
description and the diff, and write 5-10 questions that a careful reviewer of THIS
change would want answered: places where the change could break a caller, a contract, a
default, an error path or a migration. Each question must point at something in the
diff; do not restate generic checklist items.

Output JSON only:
{{"generated_questions": [{{"id": "G1", "question": "...", "rationale": "..."}}]}}

PR title: <{boundary}> {pr_title} </{boundary}>

PR description: <{boundary}> {pr_summary} </{boundary}>

Changed files: <{boundary}> {changed_files} </{boundary}>

Diff{diff_note}: <{boundary}> {diff} </{boundary}>
