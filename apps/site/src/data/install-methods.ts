/** Commands from the frozen installation and distribution guides. */
export interface InstallMethod {
  label: string;
  cmd: string;
  note: string;
}
export const INSTALL_METHODS = {
  uv: {
    label: 'uv',
    cmd: "uv pip install 'lintro[full]'",
    note: 'Python 3.11+ and an active environment. The full extra includes Python tools; external tools need separate installation. Then run <code>lintro init</code>, <code>lintro doctor</code>, and <code>lintro install --profile recommended</code>.',
  },
  pip: {
    label: 'pip',
    cmd: 'pip install lintro',
    note: 'Python 3.11+ in a virtual environment. Installs the CLI; detects tools already installed. Add <code>lintro[full]</code> for bundled Python tools.',
  },
  bun: {
    label: 'bun',
    cmd: 'bun add -g @lgtm-hq/lintro',
    note: 'Prebuilt native binary for macOS arm64 and Linux arm64/x64; no Intel macOS or Windows binary. No separate Python installation. Wrapped external tools still need installation. npm global install works too.',
  },
  brew: {
    label: 'brew',
    cmd: 'brew tap lgtm-hq/tap && brew install lintro',
    note: 'Apple silicon uses a prebuilt binary; Intel Macs install from PyPI. Use <code>lintro-full</code> for bundled Python tools.',
  },
  docker: {
    label: 'docker',
    cmd: 'docker run --rm -v "$(pwd):/code" ghcr.io/lgtm-hq/py-lintro:latest check .',
    note: 'The tools image includes external tools. For repeatable CI, pin an image version or digest; the latest tag changes. See the Docker guide for permissions and image variants.',
  },
} as const satisfies Record<string, InstallMethod>;
export type InstallKey = keyof typeof INSTALL_METHODS;
export const DEFAULT_INSTALL: InstallKey = 'uv';
export function isInstallKey(value: string): value is InstallKey {
  return Object.hasOwn(INSTALL_METHODS, value);
}
