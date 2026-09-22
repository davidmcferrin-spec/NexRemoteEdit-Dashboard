<?php
require_once __DIR__ . '/../includes/auth.php';
require_once __DIR__ . '/../includes/config.php';

require_login();
if (!has_permission('settings')) {
    json_response(['ok' => false, 'error' => 'Access denied'], 403);
}

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    json_response(['ok' => true, 'config' => nre_public_config(nre_load_config())]);
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    json_response(['ok' => false, 'error' => 'Method not allowed'], 405);
}

$body = json_decode(file_get_contents('php://input') ?: '{}', true);
if (!is_array($body)) {
    json_response(['ok' => false, 'error' => 'Invalid JSON'], 400);
}

$cfg = nre_load_config();
$section = $body['section'] ?? '';

if ($section === 'postgres') {
    $cfg['postgres']['host'] = trim((string)($body['host'] ?? $cfg['postgres']['host']));
    $cfg['postgres']['port'] = (int)($body['port'] ?? $cfg['postgres']['port']);
    $cfg['postgres']['database'] = trim((string)($body['database'] ?? $cfg['postgres']['database']));
    $cfg['postgres']['user'] = trim((string)($body['user'] ?? $cfg['postgres']['user']));
    if (isset($body['password']) && $body['password'] !== '') {
        $cfg['postgres']['password'] = (string)$body['password'];
    }
} elseif ($section === 'jump') {
    $cfg['jump']['base_url'] = rtrim(trim((string)($body['base_url'] ?? $cfg['jump']['base_url'])), '/');
    $cfg['jump']['team_id'] = trim((string)($body['team_id'] ?? $cfg['jump']['team_id']));
    if (isset($body['api_token']) && $body['api_token'] !== '') {
        $cfg['jump']['api_token'] = (string)$body['api_token'];
    }
} elseif ($section === 'telegraf') {
    if (isset($body['ingest_token']) && $body['ingest_token'] !== '') {
        $cfg['telegraf']['ingest_token'] = trim((string)$body['ingest_token']);
    }
    if (!empty($body['generate_token'])) {
        $cfg['telegraf']['ingest_token'] = bin2hex(random_bytes(32));
    }
    if (isset($body['ingest_cidrs']) && is_array($body['ingest_cidrs'])) {
        $cidrs = [];
        foreach ($body['ingest_cidrs'] as $c) {
            $c = trim((string)$c);
            if ($c !== '') {
                $cidrs[] = $c;
            }
        }
        $cfg['telegraf']['ingest_cidrs'] = $cidrs;
    }
    if (isset($body['idle_active_seconds'])) {
        $cfg['telegraf']['idle_active_seconds'] = max(5, min(86400, (int)$body['idle_active_seconds']));
    }
} elseif ($section === 'turn_servers') {
    $servers = [];
    foreach ($body['turn_servers'] ?? [] as $s) {
        if (!is_array($s)) {
            continue;
        }
        $id = trim((string)($s['id'] ?? ''));
        if ($id === '') {
            $id = 'turn-' . substr(bin2hex(random_bytes(4)), 0, 8);
        }
        $servers[] = [
            'id' => preg_replace('/[^a-zA-Z0-9._-]/', '-', $id),
            'name' => trim((string)($s['name'] ?? $id)),
            'metrics_url' => trim((string)($s['metrics_url'] ?? '')),
            'enabled' => !empty($s['enabled']),
        ];
    }
    $cfg['turn_servers'] = $servers;
} elseif ($section === 'bridge') {
    $b = $cfg['bridge'];
    foreach (['ws_port', 'ingest_port', 'jump_poll_seconds', 'turn_poll_seconds', 'retention_days', 'stale_session_hours', 'correlation_window_seconds'] as $k) {
        if (isset($body[$k])) {
            $b[$k] = max(1, (int)$body[$k]);
        }
    }
    if (isset($body['ingest_host'])) {
        $b['ingest_host'] = trim((string)$body['ingest_host']);
    }
    if (isset($body['ws_host'])) {
        $b['ws_host'] = trim((string)$body['ws_host']);
    }
    $cfg['bridge'] = $b;
} elseif ($section === 'process_watchlist') {
    $list = [];
    foreach ($body['process_watchlist'] ?? [] as $w) {
        if (!is_array($w)) {
            continue;
        }
        $match = trim((string)($w['match'] ?? ''));
        $label = trim((string)($w['label'] ?? $match));
        if ($match === '') {
            continue;
        }
        $list[] = [
            'label' => $label !== '' ? $label : $match,
            'match' => $match,
            'enabled' => !array_key_exists('enabled', $w) || !empty($w['enabled']),
        ];
    }
    $cfg['process_watchlist'] = $list;
} else {
    json_response(['ok' => false, 'error' => 'Unknown section'], 400);
}

if (!nre_save_config($cfg)) {
    json_response(['ok' => false, 'error' => 'Failed to write data/config.json'], 500);
}

$out = nre_public_config($cfg);
if ($section === 'telegraf' && !empty($body['generate_token'])) {
    $out['telegraf']['ingest_token'] = $cfg['telegraf']['ingest_token'];
    $out['telegraf']['ingest_token_once'] = true;
}
json_response(['ok' => true, 'config' => $out]);
