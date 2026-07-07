'use strict';

/**
 * Exports the absolute path to the platform-specific lintro binary.
 *
 * The `bin/lintro` file is populated by the publish workflow from the
 * Nuitka-compiled artifact for this platform. Consumers should not depend
 * on this package directly; install the `lintro` meta-package instead.
 */

const path = require('path');

module.exports.path = path.join(__dirname, 'bin', 'lintro');
