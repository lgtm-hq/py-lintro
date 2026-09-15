'use strict';

const { describe, it, expect } = require('bun:test');
const {
  PLATFORM_PACKAGES,
  UNSUPPORTED_PLATFORM_HINTS,
  packageForPlatform,
  unsupportedPlatformHint,
  supportedPlatforms,
  resolveBinary,
  ensureExecutable,
} = require('../lib/resolve.js');

describe('packageForPlatform', () => {
  it('maps every supported platform to its scoped package', () => {
    expect(packageForPlatform('darwin', 'arm64')).toBe('@lgtm-hq/lintro-darwin-arm64');
    expect(packageForPlatform('linux', 'arm64')).toBe('@lgtm-hq/lintro-linux-arm64');
    expect(packageForPlatform('linux', 'x64')).toBe('@lgtm-hq/lintro-linux-x64');
  });

  it('returns null for unsupported platforms', () => {
    expect(packageForPlatform('darwin', 'x64')).toBeNull();
    expect(packageForPlatform('win32', 'x64')).toBeNull();
    expect(packageForPlatform('linux', 'ia32')).toBeNull();
    expect(packageForPlatform('freebsd', 'arm64')).toBeNull();
  });

  it('is not fooled by prototype keys', () => {
    expect(packageForPlatform('constructor', 'x64')).toBeNull();
    expect(packageForPlatform('__proto__', 'x64')).toBeNull();
  });
});

describe('unsupportedPlatformHint', () => {
  it('points Intel Macs at Homebrew and PyPI', () => {
    const hint = unsupportedPlatformHint('darwin', 'x64');
    expect(hint).toContain('brew install lintro');
    expect(hint).toContain('pip install lintro');
  });

  it('recommends Homebrew over the PyPI route on Intel Macs', () => {
    const hint = unsupportedPlatformHint('darwin', 'x64');
    expect(hint).toContain('recommended route');
    // The recommendation must come before the PyPI alternative it outranks.
    expect(hint.indexOf('brew install lintro')).toBeLessThan(hint.indexOf('pip install lintro'));
  });

  it('warns that the Intel PyPI route builds cryptography from source', () => {
    const hint = unsupportedPlatformHint('darwin', 'x64');
    expect(hint).toContain('cryptography from source');
    expect(hint).toContain('Rust toolchain');
  });

  it('returns null for platforms with no documented alternative', () => {
    expect(unsupportedPlatformHint('win32', 'x64')).toBeNull();
    expect(unsupportedPlatformHint('constructor', 'x64')).toBeNull();
  });

  it('never hints for a platform that ships a binary', () => {
    for (const key of Object.keys(UNSUPPORTED_PLATFORM_HINTS)) {
      expect(PLATFORM_PACKAGES).not.toHaveProperty(key);
    }
  });
});

describe('supportedPlatforms', () => {
  it('lists exactly the three documented platforms', () => {
    expect(supportedPlatforms().sort()).toEqual(['darwin-arm64', 'linux-arm64', 'linux-x64']);
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

  it('points Intel Macs at Homebrew or PyPI instead of a bare failure', () => {
    let message = '';
    try {
      resolveBinary(() => ({ path: 'x' }), 'darwin', 'x64');
    } catch (err) {
      message = err.message;
    }
    expect(message).toMatch(/unsupported platform darwin-x64/);
    expect(message).toContain('brew install lintro');
    expect(message).toContain('pip install lintro');
    expect(message).toContain('Supported platforms: darwin-arm64, linux-arm64, linux-x64.');
  });

  it('leaves the Intel hint off unsupported platforms that have none', () => {
    let message = '';
    try {
      resolveBinary(() => ({ path: 'x' }), 'linux', 'riscv64');
    } catch (err) {
      message = err.message;
    }
    expect(message).toMatch(/unsupported platform linux-riscv64/);
    expect(message).not.toContain('Intel Macs');
    expect(message).not.toContain('brew install lintro');
    expect(message).not.toContain('pip install lintro');
    expect(message).toContain('Supported platforms: darwin-arm64, linux-arm64, linux-x64.');
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
    expect(() => resolveBinary(badRequire, 'darwin', 'arm64')).toThrow(
      /did not export a binary path/
    );
  });
});

describe('ensureExecutable', () => {
  const X_OK = 1;

  const fakeFs = ({ executable, chmodError, symlink, missing, directory }) => {
    const calls = [];
    return {
      calls,
      constants: { X_OK },
      accessSync(path, mode) {
        calls.push(['accessSync', path, mode]);
        if (!executable) {
          const err = new Error('EACCES: permission denied');
          err.code = 'EACCES';
          throw err;
        }
      },
      lstatSync(path) {
        calls.push(['lstatSync', path]);
        if (missing) {
          const err = new Error('ENOENT: no such file or directory');
          err.code = 'ENOENT';
          throw err;
        }
        return {
          isSymbolicLink: () => Boolean(symlink),
          isFile: () => !symlink && !directory,
        };
      },
      chmodSync(path, mode) {
        calls.push(['chmodSync', path, mode]);
        if (chmodError) {
          throw chmodError;
        }
      },
    };
  };

  it('refuses to chmod a symlink', () => {
    const fs = fakeFs({ executable: false, symlink: true });
    expect(() => ensureExecutable('/bin/x', fs)).toThrow(/not a regular file/);
    expect(fs.calls.some(([name]) => name === 'chmodSync')).toBe(false);
  });

  it('refuses to chmod something that is not a regular file', () => {
    const fs = fakeFs({ executable: false, directory: true });
    expect(() => ensureExecutable('/bin/x', fs)).toThrow(/not a regular file/);
    expect(fs.calls.some(([name]) => name === 'chmodSync')).toBe(false);
  });

  it('reports a missing binary instead of trying to repair it', () => {
    const fs = fakeFs({ executable: false, missing: true });
    expect(() => ensureExecutable('/bin/x', fs)).toThrow(/is missing/);
    expect(fs.calls.some(([name]) => name === 'chmodSync')).toBe(false);
  });

  it('leaves an executable binary untouched', () => {
    const fs = fakeFs({ executable: true });
    expect(ensureExecutable('/bin/x', fs)).toBe(false);
    expect(fs.calls).toEqual([['accessSync', '/bin/x', X_OK]]);
  });

  it('restores 0755 when the binary lost its exec bit', () => {
    // The artifact handoff between publish jobs lands every file as 0644.
    const fs = fakeFs({ executable: false });
    expect(ensureExecutable('/bin/x', fs)).toBe(true);
    expect(fs.calls).toEqual([
      ['accessSync', '/bin/x', X_OK],
      ['lstatSync', '/bin/x'],
      ['chmodSync', '/bin/x', 0o755],
    ]);
  });

  it('throws a helpful error when the mode cannot be repaired', () => {
    const fs = fakeFs({ executable: false, chmodError: new Error('EROFS: read-only file system') });
    expect(() => ensureExecutable('/ro/bin/x', fs)).toThrow(
      /platform binary at "\/ro\/bin\/x" is not executable and could not be made executable: EROFS/
    );
  });
});
