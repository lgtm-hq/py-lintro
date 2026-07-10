import { describe, expect, it } from 'vitest';
import {
  DEFAULT_THEME,
  NATIVE_THEME,
  themeOptions,
  turboThemeOptions,
  validThemeIds,
} from './themes';

describe('themeOptions', () => {
  it('includes the default theme', () => {
    expect(validThemeIds).toContain(DEFAULT_THEME);
  });

  it('exposes at least 24 turbo-themes flavors plus the native terminal theme', () => {
    expect(turboThemeOptions.length).toBeGreaterThanOrEqual(24);
    expect(themeOptions.length).toBe(turboThemeOptions.length + 1);
  });

  it('defaults to the native terminal theme', () => {
    expect(DEFAULT_THEME).toBe(NATIVE_THEME);
    expect(DEFAULT_THEME).toBe('terminal');
  });

  it('uses unique theme ids', () => {
    const ids = themeOptions.map((theme) => theme.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('provides a non-empty label for every theme', () => {
    for (const theme of themeOptions) {
      expect(theme.label.trim().length).toBeGreaterThan(0);
    }
  });
});
