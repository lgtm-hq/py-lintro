import { flavors } from "@lgtm-hq/turbo-themes/tokens";

export interface ThemeOption {
  id: string;
  label: string;
}

/** Site-native Terminal Brutalist styling — no per-theme CSS file. */
export const NATIVE_THEME = "terminal";

export const DEFAULT_THEME = NATIVE_THEME;

export const turboThemeOptions: ThemeOption[] = flavors.map((theme) => ({
  id: theme.id,
  label: theme.label,
}));

export const themeOptions: ThemeOption[] = [
  { id: NATIVE_THEME, label: "Terminal (default)" },
  ...turboThemeOptions,
];

export const validThemeIds = themeOptions.map((t) => t.id);
export const turboThemeIds = turboThemeOptions.map((t) => t.id);

export function isNativeTheme(id: string): boolean {
  return id === NATIVE_THEME;
}

export const themeAppearances: Record<string, "light" | "dark"> = {
  [NATIVE_THEME]: "dark",
  ...Object.fromEntries(flavors.map((theme) => [theme.id, theme.appearance])),
};

export interface ThemeMenuItem {
  id: string;
  label: string;
  swatch: string;
  appearance: "light" | "dark";
}

export interface ThemeMenuGroup {
  id: string;
  label: string;
  themes: ThemeMenuItem[];
}

const TERMINAL_SWATCH = "#39ff14";

const VENDOR_LABELS: Record<string, string> = {
  bulma: "Bulma",
  catppuccin: "Catppuccin",
  dracula: "Dracula",
  github: "GitHub",
  gruvbox: "Gruvbox",
  nord: "Nord",
  "rose-pine": "Rosé Pine",
  solarized: "Solarized",
  "tokyo-night": "Tokyo Night",
};

const VENDOR_ORDER = [
  "catppuccin",
  "dracula",
  "gruvbox",
  "github",
  "bulma",
  "nord",
  "solarized",
  "rose-pine",
  "tokyo-night",
] as const;

function shortThemeLabel(fullLabel: string, groupLabel: string): string {
  const normalizedGroup = groupLabel.replace(/\s*\(synced\)\s*/i, "").trim();
  if (fullLabel.toLowerCase().startsWith(normalizedGroup.toLowerCase())) {
    const stripped = fullLabel.slice(normalizedGroup.length).trim();
    return stripped || fullLabel;
  }
  return fullLabel;
}

export function buildThemeMenuGroups(): ThemeMenuGroup[] {
  const byVendor = new Map<string, ThemeMenuItem[]>();

  for (const flavor of flavors) {
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
    id: "site",
    label: "Lintro",
    themes: [
      {
        id: NATIVE_THEME,
        label: "Terminal",
        swatch: TERMINAL_SWATCH,
        appearance: "dark",
      },
    ],
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
  themeMenuItems.map((item) => [item.id, item.swatch]),
);

export const themeTriggerLabels: Record<string, string> = Object.fromEntries(
  themeMenuItems.map((item) => [item.id, item.label]),
);
