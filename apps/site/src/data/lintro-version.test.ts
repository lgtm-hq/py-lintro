import { describe, expect, it } from 'vitest';
import { LINTRO_VERSION, readLintroVersion } from './lintro-version';

describe('lintro-version', () => {
  it('reads a semantic version from the repo pyproject', () => {
    expect(LINTRO_VERSION).toMatch(/^\d+\.\d+\.\d+/);
  });

  it('fails loudly when the pyproject has no version', () => {
    expect(() => readLintroVersion('/dev/null')).toThrow(/No version field/);
  });

  it('fails loudly when an explicit path does not exist', () => {
    expect(() => readLintroVersion('/nonexistent/pyproject.toml')).toThrow(/No pyproject/);
  });
});
