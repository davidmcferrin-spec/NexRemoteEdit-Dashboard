<?php
require_once __DIR__ . '/../includes/auth.php';
require_once __DIR__ . '/../includes/config.php';

require_login();
if (!has_permission('dashboard')) {
    json_response(['ok' => false, 'error' => 'Access denied'], 403);
}

$cfg = nre_load_config();
$base = nre_public_base_url();
$tokenSet = trim((string)($cfg['telegraf']['ingest_token'] ?? '')) !== '';
$idle = (int)($cfg['telegraf']['idle_active_seconds'] ?? 120);

json_response([
    'ok' => true,
    'ingest_url' => $base . '/api/telemetry/ingest',
    'idle_script_url' => $base . '/assets/nre-idle.ps1',
    'token_set' => $tokenSet,
    'idle_active_seconds' => max(5, $idle),
    'ingest_cidrs' => $cfg['telegraf']['ingest_cidrs'] ?? [],
    'can_settings' => has_permission('settings'),
]);
