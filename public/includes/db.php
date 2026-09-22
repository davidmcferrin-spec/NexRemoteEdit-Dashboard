<?php
/**
 * PostgreSQL PDO helper. Settings come from data/config.json.
 */

declare(strict_types=1);

require_once __DIR__ . '/config.php';

function nre_pdo(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }
    $cfg = nre_load_config();
    $p = $cfg['postgres'];
    $dsn = sprintf(
        'pgsql:host=%s;port=%d;dbname=%s',
        $p['host'] ?? '127.0.0.1',
        (int)($p['port'] ?? 5432),
        $p['database'] ?? 'nre'
    );
    $pdo = new PDO($dsn, $p['user'] ?? 'nre', $p['password'] ?? '', [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    return $pdo;
}
