<?php
require_once __DIR__ . '/../includes/auth.php';
require_once __DIR__ . '/../includes/config.php';
require_once __DIR__ . '/../includes/db.php';

require_login();
if (!has_permission('mapping')) {
    json_response(['ok' => false, 'error' => 'Access denied'], 403);
}

function nre_json_list($v): array
{
    if (is_array($v)) {
        return array_values($v);
    }
    if (is_string($v) && $v !== '') {
        $d = json_decode($v, true);
        return is_array($d) ? array_values($d) : [];
    }
    return [];
}

try {
    $pdo = nre_pdo();
} catch (Throwable $e) {
    json_response(['ok' => false, 'error' => 'Database unavailable — check Settings → PostgreSQL'], 503);
}

$cfg = nre_load_config();
$turns = $cfg['turn_servers'] ?? [];

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $action = $_GET['action'] ?? 'list';
    if ($action === 'lookup') {
        $ip = trim((string)($_GET['ip'] ?? ''));
        $at = trim((string)($_GET['at'] ?? ''));
        if ($ip === '') {
            json_response(['ok' => false, 'error' => 'ip required'], 400);
        }
        $ts = $at !== '' ? $at : gmdate('c');
        $stmt = $pdo->prepare(
            "SELECT s.id, s.connection_id, s.user_email, s.hostname, s.start_time, s.end_time,
                    s.transport, i.source, i.ip::text AS ip, m.jump_display_name, m.telegraf_hostname
             FROM session_ips i
             JOIN sessions s ON s.id = i.session_id
             LEFT JOIN device_maps m ON m.jump_device_id = s.device_id
             WHERE i.ip = :ip::inet
               AND i.first_seen <= :ts::timestamptz
               AND i.last_seen >= :ts::timestamptz
             ORDER BY s.start_time DESC
             LIMIT 50"
        );
        try {
            $stmt->execute(['ip' => $ip, 'ts' => $ts]);
            $rows = $stmt->fetchAll();
        } catch (Throwable $e) {
            json_response(['ok' => false, 'error' => 'Lookup failed (invalid IP?)'], 400);
        }
        json_response(['ok' => true, 'rows' => $rows]);
    }

    $maps = $pdo->query(
        "SELECT m.*, d.online, d.last_seen, d.os
         FROM device_maps m
         LEFT JOIN devices d ON d.id = m.jump_device_id
         ORDER BY m.jump_display_name, m.jump_hostname"
    )->fetchAll();
    $ids = array_column($maps, 'id');
    $links = [];
    if ($ids) {
        $in = implode(',', array_map('intval', $ids));
        foreach ($pdo->query("SELECT device_map_id, turn_server_id FROM device_map_turn_servers WHERE device_map_id IN ($in)") as $row) {
            $links[(int)$row['device_map_id']][] = $row['turn_server_id'];
        }
    }
    foreach ($maps as &$m) {
        $m['aliases'] = nre_json_list($m['aliases'] ?? []);
        $m['turn_server_ids'] = $links[(int)$m['id']] ?? [];
    }
    unset($m);

    $tel = $pdo->query(
        "SELECT hostname, max(ts) AS last_ts
         FROM telemetry_samples
         WHERE ts > now() - interval '24 hours'
         GROUP BY hostname
         ORDER BY hostname"
    )->fetchAll();
    $mappedHosts = [];
    foreach ($maps as $m) {
        if (!empty($m['telegraf_hostname'])) {
            $mappedHosts[strtolower($m['telegraf_hostname'])] = true;
        }
        foreach ($m['aliases'] as $a) {
            $mappedHosts[strtolower((string)$a)] = true;
        }
    }
    $unmapped = [];
    foreach ($tel as $t) {
        if (empty($mappedHosts[strtolower($t['hostname'])])) {
            $unmapped[] = $t;
        }
    }

    json_response([
        'ok' => true,
        'maps' => $maps,
        'unmapped_telegraf' => $unmapped,
        'telegraf_hosts' => array_column($tel, 'hostname'),
        'turn_servers' => $turns,
    ]);
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    json_response(['ok' => false, 'error' => 'Method not allowed'], 405);
}

$body = json_decode(file_get_contents('php://input') ?: '{}', true);
if (!is_array($body)) {
    json_response(['ok' => false, 'error' => 'Invalid JSON'], 400);
}

$id = (int)($body['id'] ?? 0);
if ($id < 1) {
    json_response(['ok' => false, 'error' => 'id required'], 400);
}

$telHost = trim((string)($body['telegraf_hostname'] ?? ''));
$aliases = $body['aliases'] ?? [];
if (is_string($aliases)) {
    $aliases = array_values(array_filter(array_map('trim', explode(',', $aliases))));
}
if (!is_array($aliases)) {
    $aliases = [];
}
$policy = ($body['relay_policy'] ?? '') === 'relay_only' ? 'relay_only' : 'coturn_then_p2p';
$notes = trim((string)($body['notes'] ?? ''));
$turnIds = $body['turn_server_ids'] ?? [];
if (!is_array($turnIds)) {
    $turnIds = [];
}

$pdo->beginTransaction();
$stmt = $pdo->prepare(
    "UPDATE device_maps
     SET telegraf_hostname = :tel, aliases = :aliases::jsonb, relay_policy = :policy,
         notes = :notes, updated_at = now()
     WHERE id = :id"
);
$stmt->execute([
    'tel' => $telHost !== '' ? $telHost : null,
    'aliases' => json_encode(array_values($aliases)),
    'policy' => $policy,
    'notes' => $notes,
    'id' => $id,
]);
$pdo->prepare("DELETE FROM device_map_turn_servers WHERE device_map_id = :id")->execute(['id' => $id]);
$ins = $pdo->prepare("INSERT INTO device_map_turn_servers (device_map_id, turn_server_id) VALUES (:id, :sid)");
foreach ($turnIds as $sid) {
    $sid = trim((string)$sid);
    if ($sid !== '') {
        $ins->execute(['id' => $id, 'sid' => $sid]);
    }
}
$pdo->commit();
json_response(['ok' => true]);
