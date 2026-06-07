---
title: 'overview'
description:
  "This directory contains the foundational documents that guide Lintro's development."
category: architecture
order: 5
---

# Architecture Documentation

This directory contains the foundational documents that guide Lintro's development.

## Documents

| Document                             | Purpose                                                                |
| ------------------------------------ | ---------------------------------------------------------------------- |
| [VISION.md](./VISION.md)             | Project mission, core principles (DRY, SOLID), and success criteria    |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Technical architecture, design decisions, and component relationships  |
| [ROADMAP.md](./ROADMAP.md)           | Prioritized improvements, phased development plan, and success metrics |

## Quick Reference

### Core Principles

1. **DRY** - Single authoritative representation for every piece of knowledge
2. **SOLID** - Single responsibility, open/closed, Liskov substitution, interface
   segregation, dependency inversion
3. **Maintainability** - Readable code over clever code
4. **File discipline** - Target 300 lines, max 500 lines per file
5. **No obfuscation** - Fix issues properly, never suppress linter warnings
6. **Test everything** - Minimum 70% coverage, target 90%

### Quality Standards

| Metric           | Threshold | Target    |
| ---------------- | --------- | --------- |
| Test Coverage    | ≥ 70%     | 90%       |
| Type Annotations | ≥ 95%     | 100%      |
| File Length      | ≤ 500     | 300 lines |

### Current Priorities

1. **P0:** Error handling audit (eliminate silent failures)
2. **P0:** Test coverage to 70%
3. **P1:** Generic parser factory (reduce duplication)
4. **P1:** Standalone binary distribution

## When to Reference These Documents

- **Starting a new feature?** Check VISION.md for principles, ARCHITECTURE.md for
  patterns
- **Adding a new tool?** Follow patterns in ARCHITECTURE.md, check ROADMAP.md for
  priority
- **Refactoring code?** Ensure changes align with VISION.md principles
- **Making architectural decisions?** Use the decision framework in VISION.md

## Updating These Documents

These documents should be updated when:

- Major architectural decisions are made
- Project priorities shift significantly
- New phases of development begin
- Significant milestones are reached

All changes should be discussed and documented in pull requests.
