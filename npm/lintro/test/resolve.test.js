'use strict';

const { describe, it, expect } = require('bun:test');
const {
  PLATFORM_PACKAGES,
  packageForPlatform,
  supportedPlatforms,
  resolveBinary,
} = require('../lib/resolve.js');

describe('packageForPlatform', () => {
  it('maps every supported platform to its scoped package', () => {
    expect(packageForPlatform('darwin', 'arm64')).toBe('@lgtm-hq/lintro-darwin-arm64');
    expect(packageForPlatform('darwin', 'x64')).toBe('@lgtm-hq/lintro-darwin-x64');
    expect(packageForPlatform('linux', 'arm64')).toBe('@lgtm-hq/lintro-linux-arm64');
    expect(packageForPlatform('linux', 'x64')).toBe('@lgtm-hq/lintro-linux-x64');
  });

  it('returns null for unsupported platforms', () => {
    expect(packageForPlatform('win32', 'x64')).toBeNull();
    expect(packageForPlatform('linux', 'ia32')).toBeNull();
    expect(packageForPlatform('freebsd', 'arm64')).toBeNull();
  });

  it('is not fooled by prototype keys', () => {
    expect(packageForPlatform('constructor', 'x64')).toBeNull();
    expect(packageForPlatform('__proto__', 'x64')).toBeNull();
  });
});

describe('supportedPlatforms', () => {
  it('lists exactly the four documented platforms', () => {
    expect(supportedPlatforms().sort()).toEqual([
      'darwin-arm64',
      'darwin-x64',
      'linux-arm64',
      'linux-x64',
    ]);
  });

  it('matches the PLATFORM_PACKAGES keys', () => {
    expect(supportedPlatforms().sort()).toEqual(Object.keys(PLATFORM_PACKAGES).sort());
  });
});

describe('resolveBinary', () => {
  it('returns the path exported by the resolved platform package', () => {
    const fakeRequire = (name) => {
      expect(name).toBe('@lgtm-hq/lintro-linux-x64');
      return { path: '/somewhere/node_modules/@lgtm-hq/lintro-linux-x64/bin/lintro' };
    };
    const result = resolveBinary(fakeRequire, 'linux', 'x64');
    expect(result).toBe('/somewhere/node_modules/@lgtm-hq/lintro-linux-x64/bin/lintro');
  });

  it('throws a helpful error for unsupported platforms', () => {
    expect(() => resolveBinary(() => ({ path: 'x' }), 'win32', 'x64')).toThrow(
      /unsupported platform win32-x64/
    );
  });

  const moduleNotFoundRequire = () => {
    const err = new Error('Cannot find module');
    err.code = 'MODULE_NOT_FOUND';
    throw err;
  };

  it('throws when the platform package is not installed', () => {
    expect(() => resolveBinary(moduleNotFoundRequire, 'darwin', 'arm64')).toThrow(
      /platform package "@lgtm-hq\/lintro-darwin-arm64" is not installed/
    );
  });

  it('mentions the cross-platform lockfile pitfall when not installed', () => {
    expect(() => resolveBinary(moduleNotFoundRequire, 'linux', 'x64')).toThrow(
      /lockfile was generated on a different OS/
    );
  });

  it('rethrows non-MODULE_NOT_FOUND errors unchanged', () => {
    const brokenPackageRequire = () => {
      throw new Error('boom from inside the platform package');
    };
    expect(() => resolveBinary(brokenPackageRequire, 'linux', 'x64')).toThrow(
      /boom from inside the platform package/
    );
  });

  it('throws when the platform package exports no path', () => {
    const badRequire = () => ({});
    expect(() => resolveBinary(badRequire, 'darwin', 'x64')).toThrow(
      /did not export a binary path/
    );
  });
});
