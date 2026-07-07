<?php

declare(strict_types=1);

/**
 * Sample PHP file with seeded PHPStan violations.
 *
 * These issues are intentional and are used to validate lintro's PHPStan
 * integration. They are detectable at PHPStan's default analysis level.
 */

function add(int $a, int $b): int
{
    return $a + $b;
}

// Violation: function invoked with too few arguments (arguments.count).
echo add(1);

// Violation: call to an undefined function (function.notFound).
$result = nonExistentFunction();

echo $result;
