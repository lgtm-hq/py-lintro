# Architecture Documentation

These documents explain why Lintro is built the way it is: its purpose, its structure,
the decisions on record and where the project is heading. They are written for
contributors and for anyone evaluating how the tool works inside.

To use Lintro, start with [getting started](../getting-started.md) instead.

## Documents

Each document says what it is good for and how current it is. "Checked" means its
statements about the product were verified against Lintro 0.180.34.

| Document                                        | What it answers                                              | Status                                       |
| ----------------------------------------------- | ------------------------------------------------------------ | -------------------------------------------- |
| [Vision](./VISION.md)                           | What Lintro is for, its principles, and progress on its aims | Checked; future ideas are labelled           |
| [Architecture](./ARCHITECTURE.md)               | Layers, components, design decisions and package layout      | Checked; unbuilt ideas are labelled          |
| [Roadmap](./ROADMAP.md)                         | The original phased plan                                     | Historical; priorities now live in a backlog |
| [AI review execution](./AI-REVIEW-EXECUTION.md) | How AI review resolves its configuration and runs            | Design record for the AI subsystem           |
| [Decision records](../adr/README.md)            | Individual architecture decisions and their alternatives     | One record per decision                      |
| [Design notes](../design/README.md)             | Evaluations of approaches that were considered               | May describe options that were not adopted   |
| [Security](../security/README.md)               | Security requirements and assurance practices                | Maintained with the release process          |

## Quick Reference

### Core Principles

1. **DRY** - One authoritative home for every piece of knowledge
2. **SOLID** - Single responsibility, open/closed, Liskov substitution, interface
   segregation, dependency inversion
3. **Maintainability** - Readable code over clever code
4. **File discipline** - Split large modules by responsibility
5. **No obfuscation** - Fix issues properly instead of suppressing them
6. **Test everything** - New features and bug fixes come with tests

The [vision](./VISION.md#core-principles) explains each one and names the gate that
enforces it, where there is one.

### Quality Standards

Limits the build enforces in 0.180.34:

| Metric                  | Enforced limit                      |
| ----------------------- | ----------------------------------- |
| Test coverage           | At least 81%                        |
| Cyclomatic complexity   | At most 15 per function             |
| Function arguments      | At most 8                           |
| Statements per function | At most 50                          |
| Module length           | Warning above 800 lines             |
| Import direction        | Two `import-linter` layer contracts |

The full list is in the [vision](./VISION.md#quality-standards).

### Current Priorities

Current priorities live in the
[ordered GitHub backlog](https://github.com/lgtm-hq/py-lintro/issues/2288), not in these
documents. The [roadmap page](./ROADMAP.md) keeps the original plan as a record.

Two items the original plan listed as future work exist in 0.180.34: standalone binaries
and parallel tool execution. The [architecture](./ARCHITECTURE.md) document describes
both.

## When to Reference These Documents

- **Starting a new feature?** Read the [vision](./VISION.md) for principles and the
  [architecture](./ARCHITECTURE.md) for the patterns to follow
- **Adding a new tool?** Follow the plugin pattern in the
  [architecture](./ARCHITECTURE.md#1-plugin-system-lintroplugins), then the
  [plugin guide](../plugins.md)
- **Changing how packages depend on each other?** Check the
  [import layers](./ARCHITECTURE.md#decision-7-enforced-import-layers) first
- **Making an architectural decision?** Use the
  [decision framework](./VISION.md#decision-framework) and record the outcome as an
  [ADR](../adr/README.md)

## Updating These Documents

Update these documents in the same pull request as the change they describe:

- When an architectural decision is made or reversed
- When an idea listed as not built is implemented, or dropped
- When an enforced limit changes

State what a release does, name the release it was checked against, and keep plans
separate from shipped behavior.
