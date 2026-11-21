import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { buildConfiguration, DEFAULT_BUILDER } from './config-builder';

describe('configuration builder', () => {
  it('emits a usable YAML starter with AI explicitly disabled', () => {
    const config = parse(buildConfiguration(DEFAULT_BUILDER));
    expect(config.execution.enabled_tools).toContain('ruff');
    expect(config.enforce.line_length).toBe(88);
    expect(config.ai).toEqual({ enabled: false });
    expect(config.tools).toBeUndefined();
  });

  it('keeps shared formatting settings out of a stack without Ruff or Black', () => {
    const config = parse(buildConfiguration({ ...DEFAULT_BUILDER, groups: ['web'] }));
    expect(config.execution.enabled_tools).toContain('prettier');
    expect(config.enforce).toBeUndefined();
  });

  it('generates explicit per-feature settings and omits API costs for CLI transport', () => {
    const config = parse(
      buildConfiguration({
        ...DEFAULT_BUILDER,
        aiEnabled: true,
        summary: false,
        review: true,
        transport: 'cli',
        provider: 'openai',
      })
    );
    expect(config.ai).toEqual({
      enabled: true,
      lint: false,
      review: true,
      transport: 'cli',
      provider: 'openai',
    });
  });

  it('retains the selected API cap without including credentials', () => {
    const yaml = buildConfiguration({ ...DEFAULT_BUILDER, aiEnabled: true, maxCost: 1.25 });
    expect(parse(yaml).ai.max_cost_usd).toBe(1.25);
    expect(yaml).not.toContain('api_key:');
  });

  it.each([0, 501, 88.5, Number.NaN])('rejects invalid shared width %s', (lineLength) => {
    expect(() => buildConfiguration({ ...DEFAULT_BUILDER, lineLength })).toThrow(
      'whole line length'
    );
  });

  it.each([0, -1, Number.POSITIVE_INFINITY])('rejects invalid cost %s', (maxCost) => {
    expect(() => buildConfiguration({ ...DEFAULT_BUILDER, maxCost })).toThrow('positive API cost');
  });

  it('rejects an empty or unrecognized stack instead of emitting an all-tools allowlist', () => {
    expect(() => buildConfiguration({ ...DEFAULT_BUILDER, groups: [] })).toThrow('at least one');
    expect(() => buildConfiguration({ ...DEFAULT_BUILDER, groups: ['unknown'] })).toThrow(
      'at least one'
    );
  });
});
