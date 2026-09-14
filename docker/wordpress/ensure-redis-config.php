<?php
declare(strict_types=1);

$path = '/var/www/html/wp-config.php';
$text = @file_get_contents($path);
if ($text === false) {
    fwrite(STDERR, "wp-config.php not readable\n");
    exit(1);
}
if (strpos($text, 'WP_REDIS_PASSWORD') !== false) {
    exit(0);
}
$needle = "define( 'WP_REDIS_PORT', 6379 );";
$line = $needle . "\nif ( ! defined( 'WP_REDIS_PASSWORD' ) ) define( 'WP_REDIS_PASSWORD', getenv_docker( 'REDIS_PASSWORD', '' ) );";
$updated = str_replace($needle, $line, $text, $count);
if ($count !== 1 || file_put_contents($path, $updated) === false) {
    fwrite(STDERR, "wp-config.php Redis anchor not patched\n");
    exit(1);
}
exit(0);
