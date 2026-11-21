import { describe, expect, it } from 'vitest';
import { flavors } from '@lgtm-hq/turbo-themes/tokens';
import { existsSync, readFileSync } from 'node:fs';
import {
  DEFAULT_DARK_THEME,
  DEFAULT_THEME,
  themeAppearances,
  themeMenuGroups,
  themeOptions,
  validThemeIds,
} from './themes';
describe('exported TurboThemes integration', () => {
  it('preserves every installed flavor and its real identity', () => {
    expect(themeOptions).toEqual(flavors.map(({ id, label }) => ({ id, label })));
    expect(new Set(validThemeIds).size).toBe(flavors.length);
    expect(
      themeMenuGroups
        .flatMap((group) => group.themes)
        .map((theme) => theme.id)
        .sort()
    ).toEqual(flavors.map((theme) => theme.id).sort());
  });
  it('resolves system preferences to exported light and dark themes', () => {
    expect(themeAppearances[DEFAULT_THEME]).toBe('light');
    expect(themeAppearances[DEFAULT_DARK_THEME]).toBe('dark');
    expect(validThemeIds).toContain(DEFAULT_THEME);
    expect(validThemeIds).toContain(DEFAULT_DARK_THEME);
  });
  it('uses stylesheets actually shipped by the installed package', () => {
    for (const id of validThemeIds) {
      const path = `node_modules/@lgtm-hq/turbo-themes/packages/css/dist/themes/${id}.css`;
      expect(existsSync(path)).toBe(true);
      expect(readFileSync(path, 'utf8')).toContain(`[data-theme="${id}"]`);
    }
  });
});
