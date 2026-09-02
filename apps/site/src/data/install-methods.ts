/** Install methods shown by the homepage picker (server-rendered default and client switcher). */

export interface InstallMethod {
  label: string;
  cmd: string;
  /** Trusted HTML fragment rendered under the command. */
  note: string;
}

export const INSTALL_METHODS = {
  uv: {
    label: 'uv',
    cmd: 'uv pip install lintro',
    note: "Python 3.11+. Add <code>'lintro[full]'</code> to bundle ruff, black, mypy, bandit, pydoclint and yamllint. Then <code>lintro init &amp;&amp; lintro doctor &amp;&amp; lintro install --profile recommended</code>.",
  },
  pip: {
    label: 'pip',
    cmd: 'pip install lintro',
    note: "Python 3.11+. Add <code>'lintro[ai]'</code> for AI features or <code>'lintro[mcp]'</code> for the MCP server. Then <code>lintro init &amp;&amp; lintro doctor</code>.",
  },
  bun: {
    label: 'bun',
    cmd: 'bun add -g @lgtm-hq/lintro',
    note: 'Self-contained native binary. No Python required. Builds for darwin-arm64, darwin-x64, linux-arm64 and linux-x64. Works with <code>npm i -g</code> too.',
  },
  brew: {
    label: 'brew',
    cmd: 'brew tap lgtm-hq/tap && brew install lintro',
    note: 'Use <code>brew install lintro-full</code> to bundle the Python tools. Then <code>lintro install --profile recommended</code> for the rest.',
  },
  docker: {
    label: 'docker',
    cmd: 'docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check',
    note: 'Every tool preinstalled. Use <code>ghcr.io/lgtm-hq/py-lintro-base</code> for a CLI-only image.',
  },
} as const satisfies Record<string, InstallMethod>;

export type InstallKey = keyof typeof INSTALL_METHODS;

export const DEFAULT_INSTALL: InstallKey = 'uv';

export function isInstallKey(value: string): value is InstallKey {
  return Object.hasOwn(INSTALL_METHODS, value);
}
