import { describe, expect, it } from 'vitest';
import { TOOLS } from '../data/tools-catalog';
import { toolFamily, toolCommand } from './catalog-detail';

const ruff = TOOLS.find((tool) => tool.name === 'ruff')!;

describe('tool explorer metadata', () => {
  it.each([
    { patterns: ['*.py', '*.pyi'], category: 'lint', expected: 'Python' },
    { patterns: ['Cargo.lock'], category: 'sec', expected: 'Rust' },
    { patterns: ['*.go'], category: 'lint', expected: 'Go' },
    { patterns: ['*.vue'], category: 'type', expected: 'Web' },
    { patterns: ['*.bash'], category: 'lint', expected: 'Shell' },
    { patterns: ['*.md'], category: 'lint', expected: 'Docs' },
    { patterns: ['*'], category: 'sec', expected: 'Security' },
    { patterns: [], category: 'ai', expected: 'AI' },
    { patterns: ['*.toml'], category: 'lint', expected: 'Project' },
  ] as const)('derives $expected from declared scope', ({ patterns, category, expected }) => {
    expect(toolFamily({ ...ruff, patterns, category })).toBe(expected);
  });
  it('keeps the registered identifier in commands even when the route uses hyphens', () => {
    const tool = TOOLS.find((item) => item.name === 'pip-audit')!;
    expect(toolCommand(tool)).toBe('lintro check . --tools ' + tool.registeredName);
    expect(toolCommand(tool, true)).toBe(toolCommand(tool));
  });
  it('offers formatting only for a tool that declares fix support', () => {
    expect(toolCommand(ruff, true)).toBe('lintro format . --tools ruff');
  });
  it('keeps advisory analysis under review', () => {
    const tool = TOOLS.find((item) => item.category === 'ai')!;
    expect(toolCommand(tool)).toBe(
      'lintro review --advisory-only --tool-options idiom-review:enabled=true'
    );
  });
});
