<?php
/**
 * App settings — data/config.json only. No .env.
 */

declare(strict_types=1);

const NRE_CONFIG_FILE = __DIR__ . '/../../data/config.json';

function nre_default_config(): array
{
    return [
        'postgres' => [
            'host' => '127.0.0.1',
            'port' => 5432,
            'database' => 'nre',
            'user' => 'nre',
            'password' => '',
        ],
        'jump' => [
            'base_url' => 'https://api.jumpdesktop.com',
            'team_id' => '',
            'api_token' => '',
        ],
        'telegraf' => [
            'ingest_token' => '',
            'ingest_cidrs' => ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.1/32'],
            'idle_active_seconds' => 120,
        ],
        'turn_servers' => [],
        'process_watchlist' => nre_default_watchlist(),
        'bridge' => [
            'ws_host' => '0.0.0.0',
            'ws_port' => 8765,
            'ingest_host' => '127.0.0.1',
            'ingest_port' => 8766,
            'jump_poll_seconds' => 45,
            'turn_poll_seconds' => 15,
            'retention_days' => 90,
            'stale_session_hours' => 18,
            'correlation_window_seconds' => 90,
        ],
    ];
}

function nre_default_watchlist(): array
{
    return [
        ['label' => 'Premiere Pro', 'match' => 'premiere', 'enabled' => true],
        ['label' => 'After Effects', 'match' => 'afterfx', 'enabled' => true],
        ['label' => 'Media Encoder', 'match' => 'media encoder', 'enabled' => true],
        ['label' => 'Photoshop', 'match' => 'photoshop', 'enabled' => true],
        ['label' => 'DaVinci Resolve', 'match' => 'resolve', 'enabled' => true],
    ];
}

function nre_config_dir(): string
{
    return dirname(NRE_CONFIG_FILE);
}

function nre_deep_merge(array $base, array $over): array
{
    foreach ($over as $k => $v) {
        if (is_array($v) && isset($base[$k]) && is_array($base[$k]) && array_is_list($v) === false && array_is_list($base[$k]) === false) {
            $base[$k] = nre_deep_merge($base[$k], $v);
        } else {
            $base[$k] = $v;
        }
    }
    return $base;
}

function nre_ensure_config(): void
{
    $dir = nre_config_dir();
    if (!is_dir($dir)) {
        mkdir($dir, 0750, true);
    }
    if (!file_exists(NRE_CONFIG_FILE)) {
        nre_save_config(nre_default_config());
    }
}

function nre_load_config(): array
{
    nre_ensure_config();
    $raw = file_get_contents(NRE_CONFIG_FILE);
    $data = json_decode($raw ?: '{}', true);
    if (!is_array($data)) {
        $data = [];
    }
    return nre_deep_merge(nre_default_config(), $data);
}

function nre_save_config(array $data): bool
{
    $dir = nre_config_dir();
    if (!is_dir($dir)) {
        mkdir($dir, 0750, true);
    }
    $merged = nre_deep_merge(nre_default_config(), $data);
    $json = json_encode($merged, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    if ($json === false) {
        return false;
    }
    $tmp = NRE_CONFIG_FILE . '.tmp';
    if (file_put_contents($tmp, $json, LOCK_EX) === false) {
        return false;
    }
    return rename($tmp, NRE_CONFIG_FILE);
}

function nre_secret_meta(?string $value): array
{
    $value = (string)$value;
    if ($value === '') {
        return ['set' => false, 'hint' => ''];
    }
    $len = strlen($value);
    $hint = $len <= 4 ? '****' : ('…' . substr($value, -4));
    return ['set' => true, 'hint' => $hint];
}

function nre_public_config(array $cfg): array
{
    $out = $cfg;
    $out['jump']['api_token'] = '';
    $out['jump']['api_token_meta'] = nre_secret_meta($cfg['jump']['api_token'] ?? '');
    $out['postgres']['password'] = '';
    $out['postgres']['password_meta'] = nre_secret_meta($cfg['postgres']['password'] ?? '');
    $out['telegraf']['ingest_token'] = '';
    $out['telegraf']['ingest_token_meta'] = nre_secret_meta($cfg['telegraf']['ingest_token'] ?? '');
    return $out;
}

function nre_public_base_url(): string
{
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
        || (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https');
    $host = $_SERVER['HTTP_HOST'] ?? 'localhost';
    return ($https ? 'https' : 'http') . '://' . $host;
}

function nre_dsn(array $cfg): string
{
    $p = $cfg['postgres'];
    $host = $p['host'] ?? '127.0.0.1';
    $port = (int)($p['port'] ?? 5432);
    $db = $p['database'] ?? 'nre';
    $user = $p['user'] ?? 'nre';
    $pass = $p['password'] ?? '';
    return sprintf(
        'pgsql:host=%s;port=%d;dbname=%s;user=%s;password=%s',
        $host,
        $port,
        $db,
        $user,
        $pass
    );
}
