import { flavors } from '@lgtm-hq/turbo-themes/tokens';

export interface ThemeOption {
  id: string;
  label: string;
}

/** Site-native Workbench themes — tokens live in src/styles/workbench-theme.css. */
export const NATIVE_THEME_META = [
  { id: 'workbench', label: 'Workbench', appearance: 'light' },
  { id: 'workbench-dark', label: 'Workbench Dark', appearance: 'dark' },
] as const satisfies readonly { id: string; label: string; appearance: 'light' | 'dark' }[];

export type NativeTheme = (typeof NATIVE_THEME_META)[number]['id'];

export const NATIVE_THEMES: readonly NativeTheme[] = NATIVE_THEME_META.map((theme) => theme.id);

/** Default theme for viewers whose OS prefers a light scheme, or expresses no preference. */
export const DEFAULT_THEME: NativeTheme = 'workbench';

/** Default theme for viewers whose OS prefers a dark scheme. */
export const DEFAULT_DARK_THEME: NativeTheme = 'workbench-dark';

const WORKBENCH_SWATCH = '#e9a21b';

/** turbo-themes flavors excluding ids reserved by the site-native themes. */
const siteTurboFlavors = flavors.filter(
  (theme) => !(NATIVE_THEMES as readonly string[]).includes(theme.id)
);

export const turboThemeOptions: ThemeOption[] = siteTurboFlavors.map((theme) => ({
  id: theme.id,
  label: theme.label,
}));

export const nativeThemeOptions: ThemeOption[] = NATIVE_THEME_META.map(({ id, label }) => ({
  id,
  label,
}));

export const themeOptions: ThemeOption[] = [...nativeThemeOptions, ...turboThemeOptions];

export const validThemeIds = themeOptions.map((t) => t.id);
export const turboThemeIds = turboThemeOptions.map((t) => t.id);
export const nativeThemeIds: readonly string[] = NATIVE_THEMES;

export function isNativeTheme(id: string): id is NativeTheme {
  return (NATIVE_THEMES as readonly string[]).includes(id);
}

export const themeAppearances: Record<string, 'light' | 'dark'> = {
  ...Object.fromEntries(NATIVE_THEME_META.map((theme) => [theme.id, theme.appearance])),
  ...Object.fromEntries(siteTurboFlavors.map((theme) => [theme.id, theme.appearance])),
};

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

function shortThemeLabel(fullLabel: string, groupLabel: string): string {
  const normalizedGroup = groupLabel.replace(/\s*\(synced\)\s*/i, '').trim();
  if (fullLabel.toLowerCase().startsWith(normalizedGroup.toLowerCase())) {
    const stripped = fullLabel.slice(normalizedGroup.length).trim();
    return stripped || fullLabel;
  }
  return fullLabel;
}

export function buildThemeMenuGroups(): ThemeMenuGroup[] {
  const byVendor = new Map<string, ThemeMenuItem[]>();

  for (const flavor of siteTurboFlavors) {
    const groupLabel = VENDOR_LABELS[flavor.vendor] ?? flavor.vendor;
    const items = byVendor.get(flavor.vendor) ?? [];
    items.push({
      id: flavor.id,
      label: shortThemeLabel(flavor.label, groupLabel),
      swatch: flavor.tokens.brand.primary,
      appearance: flavor.appearance,
    });
    byVendor.set(flavor.vendor, items);
  }

  const siteGroup: ThemeMenuGroup = {
    id: 'site',
    label: 'Lintro',
    themes: NATIVE_THEME_META.map((theme) => ({ ...theme, swatch: WORKBENCH_SWATCH })),
  };

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

  return [siteGroup, ...turboGroups];
}

export const themeMenuGroups = buildThemeMenuGroups();

export const themeMenuItems = themeMenuGroups.flatMap((group) => group.themes);

export const themeSwatches: Record<string, string> = Object.fromEntries(
  themeMenuItems.map((item) => [item.id, item.swatch])
);

export const themeTriggerLabels: Record<string, string> = Object.fromEntries(
  themeMenuItems.map((item) => [item.id, item.label])
);
