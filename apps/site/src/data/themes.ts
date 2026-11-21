import { flavors } from '@lgtm-hq/turbo-themes/tokens';
import { DEFAULT_DARK_THEME, DEFAULT_THEME } from './theme-defaults';

export interface ThemeOption {
  id: string;
  label: string;
}

/** Real exported TurboThemes defaults; system mode resolves to one of these ids. */
export { DEFAULT_DARK_THEME, DEFAULT_THEME };
const siteTurboFlavors = flavors;
export const turboThemeOptions: ThemeOption[] = flavors.map(({ id, label }) => ({ id, label }));
export const themeOptions = turboThemeOptions;
export const validThemeIds = themeOptions.map((theme) => theme.id);
export const turboThemeIds = validThemeIds;
export const themeAppearances: Record<string, 'light' | 'dark'> = Object.fromEntries(
  flavors.map((theme) => [theme.id, theme.appearance])
);

export interface ThemeMenuItem {
  id: string;
  label: string;
  swatch: string;
  appearance: 'light' | 'dark';
}

export interface ThemeMenuGroup {
  id: string;
  label: string;
  themes: ThemeMenuItem[];
}

const VENDOR_LABELS: Record<string, string> = {
  bulma: 'Bulma',
  catppuccin: 'Catppuccin',
  dracula: 'Dracula',
  github: 'GitHub',
  gruvbox: 'Gruvbox',
  nord: 'Nord',
  'rose-pine': 'Rosé Pine',
  solarized: 'Solarized',
  'tokyo-night': 'Tokyo Night',
};

const VENDOR_ORDER = [
  'catppuccin',
  'dracula',
  'gruvbox',
  'github',
  'bulma',
  'nord',
  'solarized',
  'rose-pine',
  'tokyo-night',
] as const;

export function buildThemeMenuGroups(): ThemeMenuGroup[] {
  const byVendor = new Map<string, ThemeMenuItem[]>();

  for (const flavor of siteTurboFlavors) {
    const items = byVendor.get(flavor.vendor) ?? [];
    items.push({
      id: flavor.id,
      label: flavor.label,
      swatch: flavor.tokens.brand.primary,
      appearance: flavor.appearance,
    });
    byVendor.set(flavor.vendor, items);
  }

  const vendorOrderSet = new Set<string>(VENDOR_ORDER);
  const orderedVendors = [
    ...VENDOR_ORDER.filter((vendor) => byVendor.has(vendor)),
    ...[...byVendor.keys()].filter((vendor) => !vendorOrderSet.has(vendor)),
  ];
  const turboGroups = orderedVendors.map((vendor) => ({
    id: vendor,
    label: VENDOR_LABELS[vendor] ?? vendor,
    themes: byVendor.get(vendor) ?? [],
  }));

  return turboGroups;
}

export const themeMenuGroups = buildThemeMenuGroups();

export const themeMenuItems = themeMenuGroups.flatMap((group) => group.themes);

export const themeSwatches: Record<string, string> = Object.fromEntries(
  themeMenuItems.map((item) => [item.id, item.swatch])
);

export const themeTriggerLabels: Record<string, string> = Object.fromEntries(
  themeMenuItems.map((item) => [item.id, item.label])
);
