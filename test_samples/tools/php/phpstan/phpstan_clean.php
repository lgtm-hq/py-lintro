<?php

declare(strict_types=1);

/**
 * Sample PHP file with no PHPStan violations at the default analysis level.
 */

function multiply(int $a, int $b): int
{
    return $a * $b;
}

echo multiply(2, 3);
