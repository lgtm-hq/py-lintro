'use strict';

/**
 * Platform resolution for the lintro meta-package.
 *
 * Maps a Node `process.platform`/`process.arch` pair to the scoped
 * `@lgtm-hq/lintro-<platform>` package that ships the matching self-contained
 * binary. The resolution logic is kept pure and side-effect free so it
 * can be unit-tested without a binary present on disk.
 */

/**
 * Mapping from a `${platform}-${arch}` key to the scoped package name that
 * provides the binary for that platform. Keys use Node's `process.arch`
 * values (`x64`, `arm64`), which match the npm `cpu` field.
 *
 * @type {Readonly<Record<string, string>>}
 */
const PLATFORM_PACKAGES = Object.freeze({
  'darwin-arm64': '@lgtm-hq/lintro-darwin-arm64',
  'darwin-x64': '@lgtm-hq/lintro-darwin-x64',
  'linux-arm64': '@lgtm-hq/lintro-linux-arm64',
  'linux-x64': '@lgtm-hq/lintro-linux-x64',
});

/**
 * Resolve the scoped package name for a platform/arch pair.
 *
 * @param {string} platform - Value of `process.platform` (e.g. `"darwin"`).
 * @param {string} arch - Value of `process.arch` (e.g. `"arm64"`).
 * @returns {string | null} The scoped package name, or `null` when the
 *   platform is unsupported.
 */
function packageForPlatform(platform, arch) {
  const key = `${platform}-${arch}`;
  return Object.prototype.hasOwnProperty.call(PLATFORM_PACKAGES, key)
    ? PLATFORM_PACKAGES[key]
    : null;
}

/**
 * List of platform keys supported by the current distribution.
 *
 * @returns {string[]} Supported `${platform}-${arch}` keys.
 */
function supportedPlatforms() {
  return Object.keys(PLATFORM_PACKAGES);
}

/**
 * Resolve the absolute path to the lintro binary for the running platform.
 *
 * @param {NodeJS.Require} [requireFn] - Injectable `require` used to resolve
 *   the platform package (defaults to this module's `require`).
 * @param {string} [platform] - Override for `process.platform` (testing).
 * @param {string} [arch] - Override for `process.arch` (testing).
 * @returns {string} Absolute path to the binary.
 * @throws {Error} When the platform is unsupported or the platform package
 *   is not installed.
 */
function resolveBinary(requireFn, platform, arch) {
  const req = requireFn || require;
  const plat = platform || process.platform;
  const cpu = arch || process.arch;
  const pkg = packageForPlatform(plat, cpu);

  if (!pkg) {
    throw new Error(
      `lintro: unsupported platform ${plat}-${cpu}. ` +
        `Supported platforms: ${supportedPlatforms().join(', ')}.`
    );
  }

  let mod;
  try {
    mod = req(pkg);
  } catch (err) {
    throw new Error(
      `lintro: the platform package "${pkg}" is not installed. ` +
        'This usually means optional dependencies were skipped during ' +
        'install, or the lockfile was generated on a different OS and ' +
        "omits this platform's package (see npm/cli#4828). Reinstall with " +
        'optional dependencies enabled (e.g. `npm install lintro` or ' +
        '`bun add lintro`), or refresh the lockfile on this platform ' +
        `(e.g. \`npm install --force\`). Cause: ${err.message}`
    );
  }

  if (!mod || typeof mod.path !== 'string') {
    throw new Error(`lintro: platform package "${pkg}" did not export a binary path.`);
  }
  return mod.path;
}

module.exports = {
  PLATFORM_PACKAGES,
  packageForPlatform,
  supportedPlatforms,
  resolveBinary,
};
