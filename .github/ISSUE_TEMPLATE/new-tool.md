---
name: New tool request
about: Request support for a new linting or formatting tool
title: 'feat(tools): add <tool-name> support'
labels: ['enhancement', 'new-tool', 'needs-triage']
assignees: ''
---

## Tool

Name and link to the tool's homepage / repository.

## Language / File Types

What language or file format does this tool target? (e.g. Python, TypeScript, YAML,
Dockerfile)

## What it does

Describe what the tool checks or fixes. Explain why it is better than, or complementary
to, tools Lintro already supports for the same language.

## Distribution

How is it installed?

- [ ] Binary release (GitHub releases / direct download)
- [ ] Cargo (`cargo install`)
- [ ] npm / bun (`npm install -D`)
- [ ] PyPI (`pip install`)
- [ ] Rustup component (`rustup component add`)
- [ ] Other:

## Dogfooding plan

Lintro must lint something real in its own repository with every tool it ships. How
would this tool run against the Lintro codebase?

- [ ] I can add a repo config file so the tool checks existing source files
- [ ] The tool targets a language not present in this repo — I will add a rationale
      entry to the dogfood skip allowlist (see
      [#1510](https://github.com/lgtm-hq/py-lintro/issues/1510))

## Implementation notes (optional)

Any quirks the implementor should know: non-standard version flag, unusual output
format, known parser edge cases, etc.

## Checklist

- [ ] I have searched existing issues to avoid duplicates
- [ ] This tool would benefit the broader Lintro community
- [ ] I am willing to help implement or test this if needed
- [ ] I have read the
      [adding-a-new-tool guide](../../docs/contributing/adding-a-new-tool.md)
