import { describe, expect, it } from 'vitest';
import {
  DEFAULT_DARK_THEME,
  DEFAULT_THEME,
  NATIVE_THEMES,
  isNativeTheme,
  themeAppearances,
  themeMenuGroups,
  themeOptions,
  turboThemeOptions,
  validThemeIds,
} from './themes';

describe('themeOptions', () => {
  it('includes both default themes', () => {
    expect(validThemeIds).toContain(DEFAULT_THEME);
    expect(validThemeIds).toContain(DEFAULT_DARK_THEME);
  });

  it('exposes at least 24 turbo-themes flavors plus the two native workbench themes', () => {
    expect(turboThemeOptions.length).toBeGreaterThanOrEqual(24);
    expect(themeOptions.length).toBe(turboThemeOptions.length + NATIVE_THEMES.length);
  });

  it('defaults to the light workbench theme, with a dark workbench counterpart', () => {
    expect(DEFAULT_THEME).toBe('workbench');
    expect(DEFAULT_DARK_THEME).toBe('workbench-dark');
    expect(themeAppearances[DEFAULT_THEME]).toBe('light');
    expect(themeAppearances[DEFAULT_DARK_THEME]).toBe('dark');
  });

  it('no longer ships a site-native terminal theme', () => {
    expect(NATIVE_THEMES).not.toContain('terminal');
    expect(themeMenuGroups[0]?.themes.map((t) => t.id)).not.toContain('terminal');
  });

  it('recognises native themes', () => {
    expect(isNativeTheme('workbench')).toBe(true);
    expect(isNativeTheme('workbench-dark')).toBe(true);
    expect(isNativeTheme('dracula')).toBe(false);
  });

  it('lists the native themes first in the theme menu', () => {
    expect(themeMenuGroups[0]?.id).toBe('site');
    expect(themeMenuGroups[0]?.themes.map((t) => t.id)).toEqual([...NATIVE_THEMES]);
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
